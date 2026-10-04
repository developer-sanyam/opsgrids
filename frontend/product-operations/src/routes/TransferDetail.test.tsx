/**
 * The transfer detail screen: only the actions the status allows *and* the viewer holds the
 * permission for, the approval banner, the server's refusals shown as sent, and the dispatch and
 * receive dialogs — a partial receipt and a declared shortage, with quantities as decimal strings.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router-dom";

import { ApiError, AuthContext, PermissionProvider } from "@opsgrids/client";
import type { AuthContextValue, MeOut } from "@opsgrids/client";

import type { ApprovalRoute, TransferOut, TransferStatus } from "@/api/movementTypes";
import { location, transfer, transferLine } from "@/test-utils/movementData";
import { TransferDetailPage } from "./TransferDetail";

const getTransfer = vi.fn();
const requestTransfer = vi.fn();
const approveTransfer = vi.fn();
const rejectTransfer = vi.fn();
const cancelTransfer = vi.fn();
const dispatchTransfer = vi.fn();
const receiveTransfer = vi.fn();
const listLocations = vi.fn();
const listUoms = vi.fn();

vi.mock("@/api/movements", () => ({
  getTransfer: (...args: unknown[]) => getTransfer(...args),
  requestTransfer: (...args: unknown[]) => requestTransfer(...args),
  approveTransfer: (...args: unknown[]) => approveTransfer(...args),
  rejectTransfer: (...args: unknown[]) => rejectTransfer(...args),
  cancelTransfer: (...args: unknown[]) => cancelTransfer(...args),
  dispatchTransfer: (...args: unknown[]) => dispatchTransfer(...args),
  receiveTransfer: (...args: unknown[]) => receiveTransfer(...args),
}));

vi.mock("@/api/client", () => ({
  FULL_PAGE: 200,
  listLocations: (...args: unknown[]) => listLocations(...args),
  listUoms: (...args: unknown[]) => listUoms(...args),
}));

const USER: MeOut = {
  user_id: "user-1",
  email: "ops@example.com",
  full_name: "Asha Rao",
  organization_id: "org-1",
  membership_id: "mem-1",
  roles: [],
  amr: [],
  must_change_password: false,
  mfa_enabled: false,
  organizations: [],
};

const AUTH_VALUE: AuthContextValue = {
  status: "authenticated",
  user: USER,
  login: vi.fn(),
  verifyMfa: vi.fn(),
  selectOrg: vi.fn(),
  logout: vi.fn(),
};

const WRITE = "operations.inventory.transfer.write:ORG";
const APPROVE = "operations.inventory.transfer.approve:ORG";
const DISPATCH = "operations.inventory.transfer.dispatch:ORG";
const RECEIVE = "operations.inventory.transfer.receive:ORG";
const ALL = [WRITE, APPROVE, DISPATCH, RECEIVE];

function renderPage(grants: string[]) {
  const router = createMemoryRouter(
    [
      { path: "/inventory/transfers/:id", element: <TransferDetailPage /> },
      { path: "/inventory/transfers/:id/edit", element: <h1>Edit page</h1> },
      { path: "/inventory/transfers", element: <h1>Transfers</h1> },
    ],
    { initialEntries: ["/inventory/transfers/trf-1"] },
  );
  render(
    <AuthContext.Provider value={AUTH_VALUE}>
      <PermissionProvider grants={grants} entitlements={[]}>
        <RouterProvider router={router} />
      </PermissionProvider>
    </AuthContext.Provider>,
  );
  return router;
}

function inStatus(status: TransferStatus, route: ApprovalRoute | null, extra: Partial<TransferOut> = {}) {
  return transfer({ status, approval_route: route, ...extra });
}

const ACTION_NAMES = [
  "Request approval",
  "Edit",
  "Approve",
  "Reject",
  "Dispatch",
  "Receive",
  "Cancel transfer",
] as const;

function offered(): string[] {
  return ACTION_NAMES.filter((name) => screen.queryByRole("button", { name }) !== null);
}

beforeEach(() => {
  for (const fn of [
    getTransfer,
    requestTransfer,
    approveTransfer,
    rejectTransfer,
    cancelTransfer,
    dispatchTransfer,
    receiveTransfer,
    listLocations,
    listUoms,
  ]) {
    fn.mockReset();
  }
  listUoms.mockResolvedValue({ items: [], total: 0, page: 1, size: 200, pages: 0 });
  listLocations.mockResolvedValue([
    location({ id: "bin-dst", warehouse_id: "wh-dst", code: "DEFAULT", name: "Default bin", is_default: true }),
    location({ id: "bin-dst-2", warehouse_id: "wh-dst", code: "B-02", name: "Bin B-02" }),
  ]);
});

describe("TransferDetailPage actions", () => {
  const cases: {
    name: string;
    transfer: TransferOut;
    grants: string[];
    expected: string[];
  }[] = [
    { name: "a draft", transfer: inStatus("DRAFT", null), grants: ALL, expected: ["Request approval", "Edit", "Cancel transfer"] },
    { name: "a draft without write", transfer: inStatus("DRAFT", null), grants: [APPROVE, DISPATCH, RECEIVE], expected: [] },
    {
      name: "a direct-route request",
      transfer: inStatus("REQUESTED", "DIRECT"),
      grants: ALL,
      expected: ["Approve", "Reject", "Cancel transfer"],
    },
    {
      name: "a direct-route request without approve",
      transfer: inStatus("REQUESTED", "DIRECT"),
      grants: [WRITE],
      expected: ["Cancel transfer"],
    },
    {
      name: "a service-route request",
      transfer: inStatus("REQUESTED", "SERVICE", { awaiting_approval: true }),
      grants: ALL,
      expected: ["Cancel transfer"],
    },
    {
      name: "an approved transfer",
      transfer: inStatus("APPROVED", "AUTO"),
      grants: ALL,
      expected: ["Dispatch", "Cancel transfer"],
    },
    { name: "an approved transfer without dispatch", transfer: inStatus("APPROVED", "AUTO"), grants: [WRITE], expected: ["Cancel transfer"] },
    {
      name: "an in-transit transfer",
      transfer: inStatus("IN_TRANSIT", "AUTO", { lines: [transferLine({ dispatched: "40", in_transit: "40" })] }),
      grants: ALL,
      expected: ["Receive"],
    },
    {
      name: "an in-transit transfer without receive",
      transfer: inStatus("IN_TRANSIT", "AUTO"),
      grants: [WRITE, APPROVE, DISPATCH],
      expected: [],
    },
    { name: "a received transfer", transfer: inStatus("RECEIVED", "AUTO"), grants: ALL, expected: [] },
    { name: "a cancelled transfer", transfer: inStatus("CANCELLED", "AUTO"), grants: ALL, expected: [] },
    { name: "a rejected transfer", transfer: inStatus("REJECTED", "DIRECT"), grants: ALL, expected: [] },
  ];

  it.each(cases)("offers $expected for $name", async ({ transfer: t, grants, expected }) => {
    getTransfer.mockResolvedValue(t);
    renderPage(grants);
    await screen.findByRole("heading", { name: "TRF-000001" });
    await waitFor(() => expect(offered()).toEqual(expected));
  });
});

describe("TransferDetailPage content", () => {
  it("names the approver while the approval service decides", async () => {
    getTransfer.mockResolvedValue(
      inStatus("REQUESTED", "SERVICE", { awaiting_approval: true, approver_name: "Rohan Malhotra" }),
    );
    renderPage(ALL);
    expect(await screen.findByText("Waiting for approval from Rohan Malhotra")).toBeDefined();
  });

  it("says the approval service when no approver is named", async () => {
    getTransfer.mockResolvedValue(inStatus("REQUESTED", "SERVICE", { awaiting_approval: true }));
    renderPage(ALL);
    expect(await screen.findByText("Waiting for approval from the approval service")).toBeDefined();
  });

  it("shows quantity, dispatched, received, short and still-in-transit per line, as sent", async () => {
    getTransfer.mockResolvedValue(
      inStatus("IN_TRANSIT", "AUTO", {
        lines: [transferLine({ quantity: "40", dispatched: "40", received: "10.5", shortage: "2", in_transit: "27.5" })],
      }),
    );
    renderPage([]);
    const row = (await screen.findByText("RM-STL-4501")).closest("tr");
    if (!row) throw new Error("no row");
    const cells = within(row).getAllByRole("cell").map((c) => c.textContent);
    expect(cells.slice(1)).toEqual(["40 MT", "40", "10.5", "2", "27.5"]);
  });

  it("draws the history from the transitions, with the note quoted", async () => {
    getTransfer.mockResolvedValue(
      inStatus("CANCELLED", "AUTO", {
        cancel_reason: "Wrong warehouse",
        transitions: [
          { from_status: null, to_status: "DRAFT", actor: "Asha Rao", note: "", created_at: "2026-10-01T08:00:00Z" },
          {
            from_status: "DRAFT",
            to_status: "CANCELLED",
            actor: "Asha Rao",
            note: "Wrong warehouse",
            created_at: "2026-10-02T08:00:00Z",
          },
        ],
      }),
    );
    renderPage([]);
    expect(await screen.findByText("Asha Rao drafted the transfer")).toBeDefined();
    expect(screen.getByText("Asha Rao moved it from Draft to Cancelled")).toBeDefined();
    expect(screen.getByText("This transfer was cancelled")).toBeDefined();
  });

  it("has an error state with a retry", async () => {
    getTransfer.mockRejectedValueOnce(new ApiError(500, "The server fell over", null));
    getTransfer.mockResolvedValueOnce(inStatus("DRAFT", null));
    const user = userEvent.setup();
    renderPage([]);
    expect(await screen.findByText("Couldn't load this transfer")).toBeDefined();
    await user.click(screen.getByRole("button", { name: /try again/i }));
    expect(await screen.findByRole("heading", { name: "TRF-000001" })).toBeDefined();
  });
});

describe("TransferDetailPage decisions", () => {
  it("shows the server's refusal when the requester tries to approve their own transfer", async () => {
    getTransfer.mockResolvedValue(inStatus("REQUESTED", "DIRECT"));
    approveTransfer.mockRejectedValue(
      new ApiError(409, "x", {
        type: "https://errors.opsgrids.dev/conflict",
        status: 409,
        detail: "The person who requested a transfer cannot approve or reject it",
      }),
    );
    const user = userEvent.setup();
    renderPage(ALL);

    await user.click(await screen.findByRole("button", { name: "Approve" }));

    expect(await screen.findByText("The person who requested a transfer cannot approve or reject it")).toBeDefined();
  });

  it("moves to the approved state when an approval lands", async () => {
    getTransfer.mockResolvedValue(inStatus("REQUESTED", "DIRECT"));
    approveTransfer.mockResolvedValue(inStatus("APPROVED", "DIRECT", { approved_by: "Meera Nair" }));
    const user = userEvent.setup();
    renderPage(ALL);

    await user.click(await screen.findByRole("button", { name: "Approve" }));

    await waitFor(() => expect(offered()).toEqual(["Dispatch", "Cancel transfer"]));
  });

  it("will not reject without a reason, then sends the reason it is given", async () => {
    getTransfer.mockResolvedValue(inStatus("REQUESTED", "DIRECT"));
    rejectTransfer.mockResolvedValue(inStatus("REJECTED", "DIRECT", { decision_comment: "Not needed" }));
    const user = userEvent.setup();
    renderPage(ALL);

    await user.click(await screen.findByRole("button", { name: "Reject" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Reject transfer" }));
    expect(await within(dialog).findByText("Say why this transfer is being rejected.")).toBeDefined();
    expect(rejectTransfer).not.toHaveBeenCalled();

    await user.type(within(dialog).getByLabelText(/Reason for rejecting/), "Not needed");
    await user.click(within(dialog).getByRole("button", { name: "Reject transfer" }));

    await waitFor(() => expect(rejectTransfer).toHaveBeenCalledWith("trf-1", { comment: "Not needed" }));
    expect(await screen.findByText("This transfer was rejected")).toBeDefined();
  });

  it("cancels with a reason", async () => {
    getTransfer.mockResolvedValue(inStatus("APPROVED", "AUTO"));
    cancelTransfer.mockResolvedValue(inStatus("CANCELLED", "AUTO", { cancel_reason: "Customer changed plans" }));
    const user = userEvent.setup();
    renderPage(ALL);

    await user.click(await screen.findByRole("button", { name: "Cancel transfer" }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText(/Reason for cancelling/), "Customer changed plans");
    await user.click(within(dialog).getByRole("button", { name: "Cancel transfer" }));

    await waitFor(() => expect(cancelTransfer).toHaveBeenCalledWith("trf-1", { reason: "Customer changed plans" }));
  });

  it("requests approval from a draft and shows what the server decided", async () => {
    getTransfer.mockResolvedValue(inStatus("DRAFT", null));
    requestTransfer.mockResolvedValue(
      inStatus("REQUESTED", "SERVICE", { awaiting_approval: true, approver_name: "Rohan Malhotra" }),
    );
    const user = userEvent.setup();
    renderPage(ALL);

    await user.click(await screen.findByRole("button", { name: "Request approval" }));

    expect(await screen.findByText("Waiting for approval from Rohan Malhotra")).toBeDefined();
  });
});

describe("TransferDetailPage dispatch", () => {
  it("dispatches with an idempotency key and moves to in transit", async () => {
    getTransfer.mockResolvedValue(inStatus("APPROVED", "AUTO"));
    dispatchTransfer.mockResolvedValue(
      inStatus("IN_TRANSIT", "AUTO", { lines: [transferLine({ dispatched: "40", in_transit: "40" })] }),
    );
    const user = userEvent.setup();
    renderPage(ALL);

    await user.click(await screen.findByRole("button", { name: "Dispatch" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Dispatch transfer" }));

    await waitFor(() => expect(dispatchTransfer).toHaveBeenCalledTimes(1));
    const [id, body, key] = dispatchTransfer.mock.calls[0] as [string, unknown, string];
    expect(id).toBe("trf-1");
    expect(body).toEqual({});
    expect(typeof key).toBe("string");
    await waitFor(() => expect(offered()).toEqual(["Receive"]));
  });

  it("says what is short when the source no longer has the stock", async () => {
    getTransfer.mockResolvedValue(inStatus("APPROVED", "AUTO"));
    dispatchTransfer.mockRejectedValue(
      new ApiError(409, "x", {
        type: "https://errors.opsgrids.dev/insufficient-stock",
        status: 409,
        detail: "Cannot take 40 MT",
        errors: [
          {
            sku: "RM-STL-4501",
            location: "A-01",
            uom: "MT",
            requested: "40",
            on_hand: "30",
            reserved: "5",
            available: "25",
            reservations: [],
          },
        ] as never,
      }),
    );
    const user = userEvent.setup();
    renderPage(ALL);

    await user.click(await screen.findByRole("button", { name: "Dispatch" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Dispatch transfer" }));

    expect(await within(dialog).findByText("Not enough stock to dispatch this transfer")).toBeDefined();
    expect(
      within(dialog).getByText(/you asked for 40 MT, but only 25 MT is available \(30 MT on hand, 5 MT already reserved\)/),
    ).toBeDefined();
  });
});

describe("TransferDetailPage receive", () => {
  const inTransit = inStatus("IN_TRANSIT", "AUTO", {
    lines: [
      transferLine({ id: "line-1", quantity: "30", dispatched: "30", in_transit: "30" }),
      transferLine({
        id: "line-2",
        line_no: 2,
        sku: "CMP-REL-014",
        item_name: "24V relay",
        uom: "pcs",
        quantity: "10",
        dispatched: "10",
        in_transit: "10",
      }),
    ],
  });

  async function openReceive() {
    getTransfer.mockResolvedValue(inTransit);
    const user = userEvent.setup();
    renderPage(ALL);
    await user.click(await screen.findByRole("button", { name: "Receive" }));
    const dialog = await screen.findByRole("dialog");
    // The default bin is picked once the destination's bins have loaded.
    await waitFor(() =>
      expect((within(dialog).getByLabelText("Destination bin — RM-STL-4501") as HTMLSelectElement).value).toBe("bin-dst"),
    );
    return { user, dialog };
  }

  it("starts from everything arriving into the destination's default bin", async () => {
    const { dialog } = await openReceive();
    expect((within(dialog).getByLabelText("Received — RM-STL-4501") as HTMLInputElement).value).toBe("30");
    expect((within(dialog).getByLabelText("Short — RM-STL-4501") as HTMLInputElement).value).toBe("0");
    expect(listLocations).toHaveBeenCalledWith("wh-dst");
  });

  it("records a partial receipt with a declared shortage, sending decimal strings only", async () => {
    receiveTransfer.mockResolvedValue(inTransit);
    const { user, dialog } = await openReceive();

    const received = within(dialog).getByLabelText("Received — RM-STL-4501");
    await user.clear(received);
    await user.type(received, "27.5");
    const short = within(dialog).getByLabelText("Short — RM-STL-4501");
    await user.clear(short);
    await user.type(short, "2.5");
    // Nothing arrived on the second line yet: it stays in transit for the next receipt.
    const second = within(dialog).getByLabelText("Received — CMP-REL-014");
    await user.clear(second);
    await user.type(second, "0");

    expect(within(dialog).getByText("A shortage is written off")).toBeDefined();
    await user.click(within(dialog).getByRole("button", { name: "Record receipt" }));

    await waitFor(() => expect(receiveTransfer).toHaveBeenCalledTimes(1));
    const [id, body, key] = receiveTransfer.mock.calls[0] as [string, { lines: Record<string, unknown>[] }, string];
    expect(id).toBe("trf-1");
    expect(body).toEqual({
      lines: [{ line_id: "line-1", received_quantity: "27.5", shortage_quantity: "2.5", to_location_id: "bin-dst" }],
    });
    for (const line of body.lines) {
      expect(typeof line.received_quantity).toBe("string");
      expect(typeof line.shortage_quantity).toBe("string");
    }
    expect(typeof key).toBe("string");
  });

  it("refuses received plus short that is more than is in transit, before sending", async () => {
    const { user, dialog } = await openReceive();
    const short = within(dialog).getByLabelText("Short — RM-STL-4501");
    await user.clear(short);
    await user.type(short, "0.001");

    await user.click(within(dialog).getByRole("button", { name: "Record receipt" }));

    expect(
      await within(dialog).findByText("Only 30 MT is still in transit — received plus short can't be more."),
    ).toBeDefined();
    expect(receiveTransfer).not.toHaveBeenCalled();
  });

  it("refuses a fraction of a unit counted in whole pieces", async () => {
    listUoms.mockResolvedValue({
      items: [{ id: "u1", code: "pcs", name: "Pieces", dimension: "count", is_discrete: true, is_system: true }],
      total: 1,
      page: 1,
      size: 200,
      pages: 1,
    });
    const { user, dialog } = await openReceive();
    await waitFor(() => expect(listUoms).toHaveBeenCalled());
    const second = within(dialog).getByLabelText("Received — CMP-REL-014");
    await user.clear(second);
    await user.type(second, "2.5");
    // The "will stay in transit" hint drops away once the unit is known to be whole-only.
    await waitFor(() => expect(within(dialog).queryByText("7.5 pcs will stay in transit.")).toBeNull());

    await user.click(within(dialog).getByRole("button", { name: "Record receipt" }));

    expect(await within(dialog).findByText("pcs is counted in whole units — no decimals.")).toBeDefined();
    expect(receiveTransfer).not.toHaveBeenCalled();
  });

  it("keeps the dialog open with what was typed when the server refuses", async () => {
    receiveTransfer.mockRejectedValue(
      new ApiError(409, "x", {
        type: "https://errors.opsgrids.dev/conflict",
        status: 409,
        detail: "RM-STL-4501: only 20 is still in transit; cannot account for 30",
      }),
    );
    const { user, dialog } = await openReceive();

    await user.click(within(dialog).getByRole("button", { name: "Record receipt" }));

    expect(await within(dialog).findByText("RM-STL-4501: only 20 is still in transit; cannot account for 30")).toBeDefined();
    expect((within(dialog).getByLabelText("Received — RM-STL-4501") as HTMLInputElement).value).toBe("30");
  });
});
