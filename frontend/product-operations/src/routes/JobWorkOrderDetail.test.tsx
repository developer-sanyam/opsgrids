/**
 * The job-work order screen: status- and permission-gated actions, exact decimal strings all the
 * way through (nothing parsed to a float), the receive dialog's over-receipt check, an
 * `Idempotency-Key` on every stock-moving write that is *reused* on a retry, the insufficient-stock
 * 409 rendered as a per-line shortfall, and close's refusal shown with its numbers.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router-dom";

import { ApiError, AuthContext, PermissionProvider } from "@opsgrids/client";
import type { AuthContextValue, ProblemJson } from "@opsgrids/client";

import { challan, material, orderDetail, reconciliation } from "@/test-utils/jobwork";
import { JobWorkOrderDetailPage } from "./JobWorkOrderDetail";

const getJobWorkOrder = vi.fn();
const approveJobWorkOrder = vi.fn();
const cancelJobWorkOrder = vi.fn();
const closeJobWorkOrder = vi.fn();
const issueJobWorkOrder = vi.fn();
const receiveJobWorkOrder = vi.fn();
const requestChallanEway = vi.fn();

vi.mock("@/api/client", () => ({
  getJobWorkOrder: (...a: unknown[]) => getJobWorkOrder(...a),
  approveJobWorkOrder: (...a: unknown[]) => approveJobWorkOrder(...a),
  cancelJobWorkOrder: (...a: unknown[]) => cancelJobWorkOrder(...a),
  closeJobWorkOrder: (...a: unknown[]) => closeJobWorkOrder(...a),
  issueJobWorkOrder: (...a: unknown[]) => issueJobWorkOrder(...a),
  receiveJobWorkOrder: (...a: unknown[]) => receiveJobWorkOrder(...a),
  requestChallanEway: (...a: unknown[]) => requestChallanEway(...a),
}));

const READ = "operations.jobwork.order.read:ORG";
const WRITE = "operations.jobwork.order.write:ORG";
const ISSUE = "operations.jobwork.order.issue:ORG";
const RECEIVE = "operations.jobwork.order.receive:ORG";
const APPROVE = "operations.jobwork.order.approve:ORG";

const AUTH: AuthContextValue = {
  status: "authenticated",
  user: {
    user_id: "u1",
    email: "ops@example.com",
    full_name: "Asha Rao",
    organization_id: "org-1",
    membership_id: "m1",
    roles: [],
    amr: [],
    must_change_password: false,
    mfa_enabled: false,
    organizations: [],
  },
  login: vi.fn(),
  verifyMfa: vi.fn(),
  selectOrg: vi.fn(),
  logout: vi.fn(),
};

function renderPage(grants: string[]) {
  const router = createMemoryRouter(
    [
      { path: "/jobwork/orders/:id", element: <JobWorkOrderDetailPage /> },
      { path: "/jobwork/orders", element: <h1>Orders</h1> },
    ],
    { initialEntries: ["/jobwork/orders/jw-1"] },
  );
  return render(
    <AuthContext.Provider value={AUTH}>
      <PermissionProvider grants={grants} entitlements={[]}>
        <RouterProvider router={router} />
      </PermissionProvider>
    </AuthContext.Provider>,
  );
}

function button(name: string) {
  return screen.queryByRole("button", { name });
}

describe("JobWorkOrderDetailPage", () => {
  beforeEach(() => {
    for (const fn of [
      getJobWorkOrder,
      approveJobWorkOrder,
      cancelJobWorkOrder,
      closeJobWorkOrder,
      issueJobWorkOrder,
      receiveJobWorkOrder,
      requestChallanEway,
    ]) {
      fn.mockReset();
    }
  });

  it("shows loading, then the order with exact quantity strings", async () => {
    getJobWorkOrder.mockResolvedValue(
      orderDetail({
        materials: [
          material({ sent: "82.5", received: "30.125", rejected: "2.375", outstanding: "50" }),
        ],
        reconciliation: reconciliation({
          total_received: "30.125",
          total_rejected: "2.375",
          total_outstanding: "50",
        }),
      }),
    );
    renderPage([READ]);

    expect(screen.getByLabelText("Loading job-work order")).toBeDefined();
    expect(await screen.findByRole("heading", { name: "JW-2026-0001" })).toBeDefined();
    const row = screen.getByRole("row", { name: /FAB-GREIGE/ });
    expect(within(row).getByText("30.125")).toBeDefined();
    expect(within(row).getByText("2.375")).toBeDefined();
    expect(within(row).getByText("50")).toBeDefined();
    expect(screen.getByText(/82.5 sent = 30.125 received \+ 2.375 rejected \+ 0 shortage \+ 50 still at the vendor/)).toBeDefined();
  });

  it("shows an error with a retry when the order can't be loaded", async () => {
    const user = userEvent.setup();
    getJobWorkOrder.mockRejectedValueOnce(new Error("down"));
    getJobWorkOrder.mockResolvedValueOnce(orderDetail());
    renderPage([READ]);

    expect(await screen.findByText("Couldn't load this order")).toBeDefined();
    await user.click(screen.getByRole("button", { name: /retry|try again/i }));
    expect(await screen.findByRole("heading", { name: "JW-2026-0001" })).toBeDefined();
  });

  it("offers no action to someone who can only read", async () => {
    getJobWorkOrder.mockResolvedValue(orderDetail());
    renderPage([READ]);

    await screen.findByRole("heading", { name: "JW-2026-0001" });
    for (const name of ["Approve", "Issue to vendor", "Receive material", "Close order", "Cancel order"]) {
      expect(button(name), name).toBeNull();
    }
    expect(button("Request e-way bill")).toBeNull();
  });

  it("offers exactly the actions the status and the grants allow", async () => {
    getJobWorkOrder.mockResolvedValue(orderDetail({ status: "DRAFT", challans: [] }));
    renderPage([READ, WRITE, APPROVE]);
    await screen.findByRole("heading", { name: "JW-2026-0001" });
    expect(button("Approve")).not.toBeNull();
    expect(button("Cancel order")).not.toBeNull();
    expect(button("Issue to vendor")).toBeNull(); // not approved yet, and no issue grant
    expect(button("Receive material")).toBeNull();
  });

  it("an issued order offers receive and close, never cancel", async () => {
    getJobWorkOrder.mockResolvedValue(orderDetail({ status: "ISSUED" }));
    renderPage([READ, WRITE, RECEIVE, APPROVE]);
    await screen.findByRole("heading", { name: "JW-2026-0001" });
    expect(button("Receive material")).not.toBeNull();
    expect(button("Close order")).not.toBeNull();
    expect(button("Cancel order")).toBeNull();
    expect(button("Issue to vendor")).toBeNull();
  });

  it("a closed order offers nothing", async () => {
    getJobWorkOrder.mockResolvedValue(orderDetail({ status: "CLOSED" }));
    renderPage([READ, WRITE, ISSUE, RECEIVE, APPROVE]);
    await screen.findByRole("heading", { name: "JW-2026-0001" });
    for (const name of ["Approve", "Issue to vendor", "Receive material", "Close order", "Cancel order"]) {
      expect(button(name), name).toBeNull();
    }
  });

  it("flags an overdue order in words", async () => {
    getJobWorkOrder.mockResolvedValue(orderDetail({ is_overdue: true, days_overdue: 4 }));
    renderPage([READ]);
    expect(await screen.findByText("4 days overdue")).toBeDefined();
  });

  it("approves with an idempotency key", async () => {
    const user = userEvent.setup();
    getJobWorkOrder.mockResolvedValue(orderDetail({ status: "DRAFT", challans: [] }));
    approveJobWorkOrder.mockResolvedValue(orderDetail({ status: "APPROVED" }));
    renderPage([READ, APPROVE]);

    await user.click(await screen.findByRole("button", { name: "Approve" }));
    await waitFor(() => expect(approveJobWorkOrder).toHaveBeenCalledTimes(1));
    const [id, key] = approveJobWorkOrder.mock.calls[0] as [string, string];
    expect(id).toBe("jw-1");
    expect(key.length).toBeGreaterThanOrEqual(8);
    await waitFor(() => expect(getJobWorkOrder).toHaveBeenCalledTimes(2));
  });

  describe("issue", () => {
    it("sends the transport facts with a key, and reuses the key when the same request is retried", async () => {
      const user = userEvent.setup();
      getJobWorkOrder.mockResolvedValue(orderDetail({ status: "APPROVED", challans: [] }));
      issueJobWorkOrder
        .mockRejectedValueOnce(
          new ApiError(503, "down", { type: "x", status: 503, detail: "try again" } as ProblemJson),
        )
        .mockResolvedValueOnce(orderDetail({ status: "ISSUED" }));
      renderPage([READ, ISSUE]);

      await user.click(await screen.findByRole("button", { name: "Issue to vendor" }));
      await user.type(screen.getByLabelText("Vehicle number"), "MH12AB1234");
      await user.type(screen.getByLabelText("Distance (km)"), "90");
      await user.click(screen.getByRole("button", { name: "Issue material" }));
      await waitFor(() => expect(issueJobWorkOrder).toHaveBeenCalledTimes(1));
      expect(await screen.findByText("try again")).toBeDefined();

      await user.click(screen.getByRole("button", { name: "Issue material" }));
      await waitFor(() => expect(issueJobWorkOrder).toHaveBeenCalledTimes(2));

      const [firstId, firstBody, firstKey] = issueJobWorkOrder.mock.calls[0] as [string, object, string];
      const [, , secondKey] = issueJobWorkOrder.mock.calls[1] as [string, object, string];
      expect(firstId).toBe("jw-1");
      expect(firstBody).toEqual({ vehicle_no: "MH12AB1234", distance_km: 90 });
      expect(secondKey).toBe(firstKey); // same submission -> same key -> a replay, never a second issue
    });

    it("a changed request gets a new key", async () => {
      const user = userEvent.setup();
      getJobWorkOrder.mockResolvedValue(orderDetail({ status: "APPROVED", challans: [] }));
      issueJobWorkOrder.mockRejectedValue(
        new ApiError(409, "no", { type: "x", status: 409, detail: "nope" } as ProblemJson),
      );
      renderPage([READ, ISSUE]);

      await user.click(await screen.findByRole("button", { name: "Issue to vendor" }));
      await user.click(screen.getByRole("button", { name: "Issue material" }));
      await waitFor(() => expect(issueJobWorkOrder).toHaveBeenCalledTimes(1));
      await user.type(screen.getByLabelText("Vehicle number"), "KA01");
      await user.click(screen.getByRole("button", { name: "Issue material" }));
      await waitFor(() => expect(issueJobWorkOrder).toHaveBeenCalledTimes(2));
      const keys = issueJobWorkOrder.mock.calls.map((c) => c[2] as string);
      expect(keys[0]).not.toBe(keys[1]);
    });

    it("renders the insufficient-stock 409 as a per-line shortfall", async () => {
      const user = userEvent.setup();
      getJobWorkOrder.mockResolvedValue(orderDetail({ status: "APPROVED", challans: [] }));
      issueJobWorkOrder.mockRejectedValue(
        new ApiError(
          409,
          "short",
          {
            type: "https://errors.opsgrids.dev/insufficient-stock",
            status: 409,
            detail: "1 of 1 line(s) do not have enough available stock",
            errors: [
              { sku: "FAB-GREIGE", uom: "m", requested: "82.5", on_hand: "70", reserved: "10", available: "60" },
            ],
          } as unknown as ProblemJson,
        ),
      );
      renderPage([READ, ISSUE]);

      await user.click(await screen.findByRole("button", { name: "Issue to vendor" }));
      await user.click(screen.getByRole("button", { name: "Issue material" }));

      expect(await screen.findByText("Not enough available stock to issue this order")).toBeDefined();
      expect(screen.getByText(/FAB-GREIGE: needs 82.5 m, only 60 available \(70 on hand, 10 reserved\)/)).toBeDefined();
    });

    it("refuses a non-numeric distance before any request", async () => {
      const user = userEvent.setup();
      getJobWorkOrder.mockResolvedValue(orderDetail({ status: "APPROVED", challans: [] }));
      renderPage([READ, ISSUE]);

      await user.click(await screen.findByRole("button", { name: "Issue to vendor" }));
      await user.type(screen.getByLabelText("Distance (km)"), "far");
      await user.click(screen.getByRole("button", { name: "Issue material" }));
      expect(await screen.findByText("Whole kilometres, e.g. 120.")).toBeDefined();
      expect(issueJobWorkOrder).not.toHaveBeenCalled();
    });
  });

  describe("receive", () => {
    async function openReceive(user: ReturnType<typeof userEvent.setup>) {
      getJobWorkOrder.mockResolvedValue(orderDetail({ status: "ISSUED" }));
      renderPage([READ, RECEIVE]);
      await user.click(await screen.findByRole("button", { name: "Receive material" }));
    }

    it("sends exact decimal strings, with a key", async () => {
      const user = userEvent.setup();
      receiveJobWorkOrder.mockResolvedValue(orderDetail({ status: "PARTIALLY_RECEIVED" }));
      await openReceive(user);

      await user.type(screen.getByLabelText("Received good (m)"), "30.125");
      await user.type(screen.getByLabelText("Rejected (m)"), "2.375");
      expect(screen.getByText(/Left at vendor after this receipt:/).textContent).toMatch(/50 m/);
      await user.click(screen.getByRole("button", { name: "Record receipt" }));

      await waitFor(() => expect(receiveJobWorkOrder).toHaveBeenCalledTimes(1));
      const [id, body, key] = receiveJobWorkOrder.mock.calls[0] as [string, { lines: unknown[] }, string];
      expect(id).toBe("jw-1");
      expect(body.lines).toEqual([
        { material_id: "mat-1", received: "30.125", rejected: "2.375", shortage: "0" },
      ]);
      // strings, not numbers, on the wire
      for (const line of body.lines as Record<string, unknown>[]) {
        expect(typeof line.received).toBe("string");
      }
      expect(key.length).toBeGreaterThanOrEqual(8);
    });

    it("refuses one milli-unit more than is at the vendor, without calling the server", async () => {
      const user = userEvent.setup();
      await openReceive(user);

      await user.type(screen.getByLabelText("Received good (m)"), "82.5");
      await user.type(screen.getByLabelText("Shortage (m)"), "0.001");
      expect(screen.getByText(/Left at vendor after this receipt:/).textContent).toMatch(/-0\.001 m/);
      await user.click(screen.getByRole("button", { name: "Record receipt" }));

      expect(await screen.findByText(/82.501 m is more than the 82.5 m still at the vendor/)).toBeDefined();
      expect(receiveJobWorkOrder).not.toHaveBeenCalled();
    });

    it("accepts exactly what is outstanding", async () => {
      const user = userEvent.setup();
      receiveJobWorkOrder.mockResolvedValue(orderDetail({ status: "RECEIVED" }));
      await openReceive(user);

      await user.type(screen.getByLabelText("Received good (m)"), "82.5");
      await user.click(screen.getByRole("button", { name: "Record receipt" }));
      await waitFor(() => expect(receiveJobWorkOrder).toHaveBeenCalledTimes(1));
    });

    it("asks for something when the form is empty", async () => {
      const user = userEvent.setup();
      await openReceive(user);
      await user.click(screen.getByRole("button", { name: "Record receipt" }));
      expect(await screen.findByText(/at least one material/)).toBeDefined();
      expect(receiveJobWorkOrder).not.toHaveBeenCalled();
    });

    it("refuses a fraction of a discrete unit", async () => {
      const user = userEvent.setup();
      getJobWorkOrder.mockResolvedValue(
        orderDetail({
          status: "ISSUED",
          materials: [material({ uom: "pcs", sku: "THR-001", quantity: "20", sent: "20", outstanding: "20" })],
        }),
      );
      renderPage([READ, RECEIVE]);
      await user.click(await screen.findByRole("button", { name: "Receive material" }));
      await user.type(screen.getByLabelText("Received good (pcs)"), "2.5");
      await user.click(screen.getByRole("button", { name: "Record receipt" }));
      expect(await screen.findByText("pcs is counted in whole units")).toBeDefined();
      expect(receiveJobWorkOrder).not.toHaveBeenCalled();
    });

    it("shows the server's over-receipt refusal when another receipt got there first", async () => {
      const user = userEvent.setup();
      receiveJobWorkOrder.mockRejectedValue(
        new ApiError(409, "over", {
          type: "https://errors.opsgrids.dev/conflict",
          status: 409,
          detail: "FAB-GREIGE: 50 is more than the 20 still at the vendor",
        } as ProblemJson),
      );
      await openReceive(user);
      await user.type(screen.getByLabelText("Received good (m)"), "50");
      await user.click(screen.getByRole("button", { name: "Record receipt" }));
      expect(await screen.findByText(/50 is more than the 20 still at the vendor/)).toBeDefined();
    });

    it("only lists materials that still have something at the vendor", async () => {
      const user = userEvent.setup();
      getJobWorkOrder.mockResolvedValue(
        orderDetail({
          status: "PARTIALLY_RECEIVED",
          materials: [
            material({ id: "done", sku: "DONE-1", received: "82.5", outstanding: "0" }),
            material({ id: "open", sku: "OPEN-1" }),
          ],
        }),
      );
      renderPage([READ, RECEIVE]);
      await user.click(await screen.findByRole("button", { name: "Receive material" }));
      const dialog = screen.getByRole("dialog");
      expect(within(dialog).getByText("OPEN-1")).toBeDefined();
      expect(within(dialog).queryByText("DONE-1")).toBeNull();
    });
  });

  describe("reconcile and close", () => {
    it("explains why the order can't be closed yet", async () => {
      getJobWorkOrder.mockResolvedValue(orderDetail({ status: "PARTIALLY_RECEIVED" }));
      renderPage([READ, APPROVE]);
      expect(await screen.findByText(/Can't close yet: 82.5 is still unaccounted for at the vendor/)).toBeDefined();
      expect(screen.getByText("Not balanced")).toBeDefined();
    });

    it("shows a balanced order and closes it with a key", async () => {
      const user = userEvent.setup();
      getJobWorkOrder.mockResolvedValue(
        orderDetail({
          status: "RECEIVED",
          materials: [material({ received: "80", shortage: "2.5", outstanding: "0" })],
          reconciliation: reconciliation({
            order_status: "RECEIVED",
            total_received: "80",
            total_shortage: "2.5",
            total_outstanding: "0",
            documents_settled: true,
            balanced: true,
          }),
        }),
      );
      closeJobWorkOrder.mockResolvedValue(orderDetail({ status: "CLOSED" }));
      renderPage([READ, APPROVE]);

      expect(await screen.findByText("Balanced")).toBeDefined();
      await user.click(screen.getByRole("button", { name: "Close order" }));
      const dialog = screen.getByRole("dialog");
      await user.click(within(dialog).getByRole("button", { name: "Close order" }));
      await waitFor(() => expect(closeJobWorkOrder).toHaveBeenCalledTimes(1));
      expect((closeJobWorkOrder.mock.calls[0] as [string, string])[1].length).toBeGreaterThanOrEqual(8);
    });

    it("shows the server's refusal inside the dialog when the ledger disagrees", async () => {
      const user = userEvent.setup();
      getJobWorkOrder.mockResolvedValue(
        orderDetail({
          status: "RECEIVED",
          materials: [material({ received: "82.5", outstanding: "0" })],
          reconciliation: reconciliation({
            order_status: "RECEIVED",
            total_received: "82.5",
            total_outstanding: "0",
            documents_settled: true,
            ledger_consistent: false,
            drift: [{ item_id: "item-1", sku: "FAB-GREIGE", documents: "0", ledger: "0.001" }],
            balanced: false,
          }),
        }),
      );
      closeJobWorkOrder.mockRejectedValue(
        new ApiError(409, "no", {
          type: "https://errors.opsgrids.dev/jobwork-reconciliation-not-balanced",
          status: 409,
          detail: "JW-2026-0001 cannot be closed: the documents and the stock ledger disagree",
        } as ProblemJson),
      );
      renderPage([READ, APPROVE]);

      expect(await screen.findByText(/FAB-GREIGE: documents say 0 at the vendor, the ledger says 0.001/)).toBeDefined();
      await user.click(screen.getByRole("button", { name: "Close order" }));
      const dialog = screen.getByRole("dialog");
      await user.click(within(dialog).getByRole("button", { name: "Close order" }));
      expect(await within(dialog).findByText(/JW-2026-0001 cannot be closed:/)).toBeDefined();
    });

    it("warns when the figures the server sent do not foot", async () => {
      getJobWorkOrder.mockResolvedValue(
        orderDetail({ reconciliation: reconciliation({ total_outstanding: "82.499" }) }),
      );
      renderPage([READ]);
      expect(await screen.findByText(/These figures do not add up/)).toBeDefined();
    });
  });

  describe("cancel", () => {
    it("cancels a draft with a reason and a key", async () => {
      const user = userEvent.setup();
      getJobWorkOrder.mockResolvedValue(orderDetail({ status: "DRAFT", challans: [] }));
      cancelJobWorkOrder.mockResolvedValue(orderDetail({ status: "CANCELLED" }));
      renderPage([READ, WRITE]);

      await user.click(await screen.findByRole("button", { name: "Cancel order" }));
      await user.type(screen.getByLabelText("Reason"), "wrong vendor");
      const dialog = screen.getByRole("dialog");
      await user.click(within(dialog).getByRole("button", { name: "Cancel order" }));
      await waitFor(() => expect(cancelJobWorkOrder).toHaveBeenCalledTimes(1));
      const [id, reason, key] = cancelJobWorkOrder.mock.calls[0] as [string, string, string];
      expect([id, reason]).toEqual(["jw-1", "wrong vendor"]);
      expect(key.length).toBeGreaterThanOrEqual(8);
    });
  });

  describe("challans and the e-way hook", () => {
    it("lists the challan with its e-way state and a print link", async () => {
      getJobWorkOrder.mockResolvedValue(
        orderDetail({ challans: [challan({ eway_status: "REQUESTED" })] }),
      );
      renderPage([READ]);
      expect(await screen.findByText("DC/26-27/0001")).toBeDefined();
      expect(screen.getByText("E-way bill requested")).toBeDefined();
      const link = screen.getByRole("link", { name: "Print challan" });
      expect(link.getAttribute("href")).toBe("/jobwork/challans/ch-1/print");
    });

    it("lets someone with the issue grant request an e-way bill, with the distance", async () => {
      const user = userEvent.setup();
      getJobWorkOrder.mockResolvedValue(orderDetail());
      requestChallanEway.mockResolvedValue(challan({ eway_status: "REQUESTED" }));
      renderPage([READ, ISSUE]);

      await user.click(await screen.findByRole("button", { name: "Request e-way bill" }));
      const distance = screen.getByLabelText("Distance (km)");
      await user.clear(distance);
      await user.type(distance, "250");
      await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Request e-way bill" }));
      await waitFor(() => expect(requestChallanEway).toHaveBeenCalledTimes(1));
      expect(requestChallanEway.mock.calls[0]).toEqual([
        "ch-1",
        { distance_km: 250, vehicle_no: "MH12AB1234" },
      ]);
    });

    it("does not offer a new e-way request once a bill exists", async () => {
      getJobWorkOrder.mockResolvedValue(
        orderDetail({ challans: [challan({ eway_status: "GENERATED", eway_bill_no: "181000123456" })] }),
      );
      renderPage([READ, ISSUE]);
      expect(await screen.findByText("No. 181000123456")).toBeDefined();
      expect(button("Request e-way bill")).toBeNull();
      expect(button("Update e-way request")).toBeNull();
    });

    it("says a challan is cut at issue when there is none", async () => {
      getJobWorkOrder.mockResolvedValue(orderDetail({ status: "APPROVED", challans: [] }));
      renderPage([READ]);
      expect(await screen.findByText(/A challan is cut when the material is issued/)).toBeDefined();
    });
  });

  it("shows empty receipts with guidance", async () => {
    getJobWorkOrder.mockResolvedValue(orderDetail());
    renderPage([READ]);
    expect(await screen.findByText("Nothing received yet")).toBeDefined();
    expect(screen.getByText(/Record a receipt as the vendor returns material/)).toBeDefined();
  });
});
