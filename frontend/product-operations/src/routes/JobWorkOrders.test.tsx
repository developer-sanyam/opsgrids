/**
 * The job-work list: loading, empty, error and populated states; server-side filters (status,
 * overdue, search); permission-aware "New order"; and the create dialog sending decimal strings
 * with an idempotency key.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { InventoryItemOut, SupplierOut } from "@/api/types";
import { renderScreen } from "@/test-utils/renderScreen";
import { orderDetail, orderSummary, page } from "@/test-utils/jobwork";
import { JobWorkOrdersPage } from "./JobWorkOrders";

const listJobWorkOrders = vi.fn();
const createJobWorkOrder = vi.fn();
const searchActiveSuppliers = vi.fn();
const searchInventory = vi.fn();
const navigate = vi.fn();

vi.mock("@/api/client", () => ({
  listJobWorkOrders: (...a: unknown[]) => listJobWorkOrders(...a),
  createJobWorkOrder: (...a: unknown[]) => createJobWorkOrder(...a),
  searchActiveSuppliers: (...a: unknown[]) => searchActiveSuppliers(...a),
  searchInventory: (...a: unknown[]) => searchInventory(...a),
}));

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>("react-router-dom");
  return { ...actual, useNavigate: () => navigate };
});

const READ = "operations.jobwork.order.read:ORG";
const WRITE = "operations.jobwork.order.write:ORG";

const SUPPLIER = { id: "sup-1", code: "SUP-DYE", name: "Rainbow Dyers" } as SupplierOut;
const FABRIC = { id: "item-1", sku: "FAB-GREIGE", name: "Greige fabric", uom: "m" } as InventoryItemOut;
const THREAD = { id: "item-2", sku: "THR-001", name: "Thread", uom: "pcs" } as InventoryItemOut;

describe("JobWorkOrdersPage", () => {
  beforeEach(() => {
    for (const fn of [listJobWorkOrders, createJobWorkOrder, searchActiveSuppliers, searchInventory, navigate]) {
      fn.mockReset();
    }
    searchActiveSuppliers.mockResolvedValue([SUPPLIER]);
    searchInventory.mockResolvedValue([FABRIC, THREAD]);
  });

  it("shows a loading state, then the orders with quantities as sent", async () => {
    listJobWorkOrders.mockResolvedValue(
      page([orderSummary({ total_sent: "82.5", total_outstanding: "52.375" })]),
    );
    renderScreen(<JobWorkOrdersPage />, [READ]);

    expect(screen.getByLabelText("Loading job-work orders")).toBeDefined();
    expect(await screen.findByText("JW-2026-0001")).toBeDefined();
    expect(screen.getByText("Rainbow Dyers")).toBeDefined();
    expect(screen.getByText("52.375")).toBeDefined();
    expect(screen.getByText("At vendor", { selector: "span" })).toBeDefined();
  });

  it("flags an overdue order in words next to its status", async () => {
    listJobWorkOrders.mockResolvedValue(page([orderSummary({ is_overdue: true, days_overdue: 1 })]));
    renderScreen(<JobWorkOrdersPage />, [READ]);
    expect(await screen.findByText("1 day overdue")).toBeDefined();
  });

  it("shows an empty state with guidance for someone who can draft", async () => {
    listJobWorkOrders.mockResolvedValue(page([]));
    renderScreen(<JobWorkOrdersPage />, [READ, WRITE]);
    expect(await screen.findByText("No job-work orders yet")).toBeDefined();
    expect(screen.getByText("Draft one to send material to a vendor.")).toBeDefined();
  });

  it("shows a different empty state to a read-only user", async () => {
    listJobWorkOrders.mockResolvedValue(page([]));
    renderScreen(<JobWorkOrdersPage />, [READ]);
    expect(await screen.findByText("Orders appear here once someone drafts them.")).toBeDefined();
  });

  it("shows an error with retry", async () => {
    const user = userEvent.setup();
    listJobWorkOrders.mockRejectedValueOnce(new Error("down"));
    listJobWorkOrders.mockResolvedValueOnce(page([orderSummary()]));
    renderScreen(<JobWorkOrdersPage />, [READ]);

    expect(await screen.findByText("Couldn't load job-work orders")).toBeDefined();
    await user.click(screen.getByRole("button", { name: /retry|try again/i }));
    expect(await screen.findByText("JW-2026-0001")).toBeDefined();
  });

  it("hides New order without the write grant", async () => {
    listJobWorkOrders.mockResolvedValue(page([orderSummary()]));
    renderScreen(<JobWorkOrdersPage />, [READ]);
    await screen.findByText("JW-2026-0001");
    expect(screen.queryByRole("button", { name: /New order/ })).toBeNull();
  });

  it("filters on the server by status and by overdue", async () => {
    const user = userEvent.setup();
    listJobWorkOrders.mockResolvedValue(page([orderSummary()]));
    renderScreen(<JobWorkOrdersPage />, [READ]);
    await screen.findByText("JW-2026-0001");

    await user.click(screen.getByRole("button", { name: "Partly received" }));
    await waitFor(() =>
      expect(listJobWorkOrders).toHaveBeenLastCalledWith(
        expect.objectContaining({ status: "PARTIALLY_RECEIVED", overdue: false, page: 1 }),
      ),
    );
    await user.click(screen.getByRole("button", { name: "Overdue only" }));
    await waitFor(() =>
      expect(listJobWorkOrders).toHaveBeenLastCalledWith(expect.objectContaining({ overdue: true })),
    );
    expect(await screen.findByText("JW-2026-0001")).toBeDefined();
  });

  it("explains an empty filtered result differently", async () => {
    const user = userEvent.setup();
    listJobWorkOrders.mockResolvedValueOnce(page([orderSummary()])).mockResolvedValue(page([]));
    renderScreen(<JobWorkOrdersPage />, [READ]);
    await screen.findByText("JW-2026-0001");
    await user.click(screen.getByRole("button", { name: "Closed" }));
    expect(await screen.findByText("No orders match these filters")).toBeDefined();
  });

  describe("new order", () => {
    async function openDialog(user: ReturnType<typeof userEvent.setup>) {
      listJobWorkOrders.mockResolvedValue(page([]));
      renderScreen(<JobWorkOrdersPage />, [READ, WRITE]);
      await user.click(await screen.findByRole("button", { name: /New order/ }));
    }

    async function pick(user: ReturnType<typeof userEvent.setup>, label: string, text: string, option: string) {
      const input = screen.getAllByLabelText(label).at(-1);
      if (!input) throw new Error(`no ${label}`);
      await user.click(input);
      await user.type(input, text);
      await user.click(await screen.findByRole("option", { name: new RegExp(option) }));
    }

    it("creates a draft with decimal-string quantities and an idempotency key, then opens it", async () => {
      const user = userEvent.setup();
      createJobWorkOrder.mockResolvedValue(orderDetail({ id: "jw-9", status: "DRAFT" }));
      await openDialog(user);

      await pick(user, "Supplier", "rain", "Rainbow Dyers");
      await user.type(screen.getByLabelText("Process"), "Dyeing");
      await pick(user, "SKU", "fab", "FAB-GREIGE");
      await user.type(screen.getByLabelText("Quantity (m)"), "82.5");
      await user.click(screen.getByRole("button", { name: "Create draft" }));

      await waitFor(() => expect(createJobWorkOrder).toHaveBeenCalledTimes(1));
      const [body, key] = createJobWorkOrder.mock.calls[0] as [Record<string, unknown>, string];
      expect(body).toMatchObject({
        supplier_id: "sup-1",
        process: "Dyeing",
        due_date: null,
        qc_required: false,
        materials: [{ item_id: "item-1", quantity: "82.5" }],
      });
      expect(typeof (body.materials as { quantity: unknown }[])[0]?.quantity).toBe("string");
      expect(key.length).toBeGreaterThanOrEqual(8);
      await waitFor(() => expect(navigate).toHaveBeenCalledWith("/jobwork/orders/jw-9"));
    });

    it("refuses a fraction of a piece and a missing vendor before any request", async () => {
      const user = userEvent.setup();
      await openDialog(user);

      await user.click(screen.getByRole("button", { name: "Create draft" }));
      expect(await screen.findByText("Choose the vendor who will process the material.")).toBeDefined();

      await pick(user, "Supplier", "rain", "Rainbow Dyers");
      await pick(user, "SKU", "thr", "THR-001");
      await user.type(screen.getByLabelText("Quantity (pcs)"), "2.5");
      await user.click(screen.getByRole("button", { name: "Create draft" }));
      expect(await screen.findByText("pcs is counted in whole units")).toBeDefined();
      expect(createJobWorkOrder).not.toHaveBeenCalled();
    });

    it("refuses a material line with no quantity", async () => {
      const user = userEvent.setup();
      await openDialog(user);
      await pick(user, "Supplier", "rain", "Rainbow Dyers");
      await pick(user, "SKU", "fab", "FAB-GREIGE");
      await user.click(screen.getByRole("button", { name: "Create draft" }));
      expect(await screen.findByText("Quantity must be greater than zero.")).toBeDefined();
      expect(createJobWorkOrder).not.toHaveBeenCalled();
    });

    it("shows the server's refusal inside the dialog", async () => {
      const user = userEvent.setup();
      const { ApiError } = await import("@opsgrids/client");
      createJobWorkOrder.mockRejectedValue(
        new ApiError(409, "conflict", { type: "x", status: 409, detail: "That supplier is on hold" } as never),
      );
      await openDialog(user);
      await pick(user, "Supplier", "rain", "Rainbow Dyers");
      await pick(user, "SKU", "fab", "FAB-GREIGE");
      await user.type(screen.getByLabelText("Quantity (m)"), "5");
      await user.click(screen.getByRole("button", { name: "Create draft" }));
      expect(await screen.findByText("That supplier is on hold")).toBeDefined();
      expect(navigate).not.toHaveBeenCalled();
    });
  });
});
