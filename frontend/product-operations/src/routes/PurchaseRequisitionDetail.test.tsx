/**
 * The requisition detail: every control follows both the status and what the viewer holds, a
 * rejection needs a reason, and converting needs both write permissions and lands on the new PO.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { RequisitionOut, RequisitionStatus, SupplierOut } from "@/api/types";
import { renderRoute } from "@/test-utils/renderRoute";
import { PurchaseRequisitionDetailPage } from "./PurchaseRequisitionDetail";

const getRequisition = vi.fn();
const submitRequisition = vi.fn();
const approveRequisition = vi.fn();
const rejectRequisition = vi.fn();
const cancelRequisition = vi.fn();
const convertRequisition = vi.fn();
const listWarehouses = vi.fn();
const searchActiveSuppliers = vi.fn();

vi.mock("@/api/client", () => ({
  FULL_PAGE: 200,
  getRequisition: (...args: unknown[]) => getRequisition(...args),
  submitRequisition: (...args: unknown[]) => submitRequisition(...args),
  approveRequisition: (...args: unknown[]) => approveRequisition(...args),
  rejectRequisition: (...args: unknown[]) => rejectRequisition(...args),
  cancelRequisition: (...args: unknown[]) => cancelRequisition(...args),
  convertRequisition: (...args: unknown[]) => convertRequisition(...args),
  listWarehouses: (...args: unknown[]) => listWarehouses(...args),
  searchActiveSuppliers: (...args: unknown[]) => searchActiveSuppliers(...args),
}));

const WRITE = "operations.procurement.requisition.write:OWN";
const APPROVE = "operations.procurement.requisition.approve:ORG";
const PO_WRITE = "operations.procurement.purchase_order.write:OWN";

function requisition(status: RequisitionStatus, overrides: Partial<RequisitionOut> = {}): RequisitionOut {
  return {
    id: "req-1",
    number: "PR-0001",
    status,
    requested_by: "Asha Rao",
    requested_by_id: "user-1",
    warehouse_id: "wh-1",
    warehouse_name: "Bhiwandi DC",
    needed_by: "2026-10-20",
    reason: "Restock before the festive season",
    decision_note: null,
    submitted_at: status === "DRAFT" ? null : "2026-10-01T09:00:00Z",
    decided_at: null,
    converted_po_id: null,
    converted_po_number: null,
    created_at: "2026-09-30T09:00:00Z",
    updated_at: "2026-10-01T09:00:00Z",
    lines: [
      {
        id: "line-1",
        line_no: 1,
        inventory_item_id: "item-1",
        sku: "FAB-1",
        item_name: "Poplin",
        uom: "m",
        quantity: "82.5",
        suggested_supplier_id: null,
        est_unit_price_minor: 2450,
        note: null,
      },
    ],
    ...overrides,
  };
}

const SUPPLIER: SupplierOut = {
  id: "sup-1",
  code: "SUP-01",
  name: "Kaveri Textiles",
  category: "raw_materials",
  status: "active",
  lead_time_days: 7,
  on_time_pct: 95,
  spend_minor: 0,
  currency: "INR",
  open_pos: 0,
  contact_name: "Priya",
  contact_email: "priya@example.com",
  location: "Coimbatore",
  since: "2024-01-01",
};

function renderDetail(grants: string[]) {
  return renderRoute(
    [
      { path: "/purchase-requisitions/:id", element: <PurchaseRequisitionDetailPage /> },
      { path: "/purchase-requisitions/:id/edit", element: <h1>Edit form</h1> },
      { path: "/purchase-orders/:id", element: <h1>Purchase order detail</h1> },
    ],
    "/purchase-requisitions/req-1",
    grants,
  );
}

const names = () => screen.queryAllByRole("button").map((b) => b.textContent);

describe("PurchaseRequisitionDetailPage", () => {
  beforeEach(() => {
    for (const fn of [getRequisition, submitRequisition, approveRequisition, rejectRequisition, cancelRequisition, convertRequisition]) {
      fn.mockReset();
    }
    listWarehouses.mockResolvedValue({ items: [{ id: "wh-1", code: "WH", name: "Bhiwandi DC", location: "" }], total: 1, page: 1, size: 200, pages: 1 });
    searchActiveSuppliers.mockResolvedValue([SUPPLIER]);
  });

  it("shows the lines and the history, with no actions for a viewer who holds nothing", async () => {
    getRequisition.mockResolvedValue(requisition("SUBMITTED"));
    renderDetail([]);

    expect(await screen.findByRole("heading", { name: "PR-0001" })).toBeDefined();
    expect(screen.getByText("Poplin")).toBeDefined();
    expect(screen.getByText("82.5 m")).toBeDefined();
    expect(screen.getByText("Submitted for approval")).toBeDefined();
    for (const label of ["Approve", "Reject", "Edit", "Cancel requisition", "Submit for approval", "Convert to purchase order"]) {
      expect(names()).not.toContain(label);
    }
  });

  it("gives a requester on a draft Edit, Submit and Cancel, and no decision", async () => {
    getRequisition.mockResolvedValue(requisition("DRAFT"));
    renderDetail([WRITE]);

    await screen.findByRole("heading", { name: "PR-0001" });
    expect(names()).toEqual(expect.arrayContaining(["Edit", "Cancel requisition", "Submit for approval"]));
    expect(names()).not.toContain("Approve");
    expect(names()).not.toContain("Reject");
  });

  it("gives an approver Approve and Reject on a submitted requisition, and a plain writer neither", async () => {
    getRequisition.mockResolvedValue(requisition("SUBMITTED"));
    renderDetail([APPROVE]);
    await screen.findByRole("heading", { name: "PR-0001" });
    expect(names()).toEqual(expect.arrayContaining(["Approve", "Reject"]));
  });

  it("hides the decision from someone who can write but not approve", async () => {
    getRequisition.mockResolvedValue(requisition("SUBMITTED"));
    renderDetail([WRITE]);
    await screen.findByRole("heading", { name: "PR-0001" });
    expect(names()).not.toContain("Approve");
    expect(names()).not.toContain("Reject");
    expect(names()).toContain("Cancel requisition");
  });

  it("approves through a confirmation and reloads", async () => {
    getRequisition.mockResolvedValueOnce(requisition("SUBMITTED")).mockResolvedValue(requisition("APPROVED"));
    approveRequisition.mockResolvedValue(requisition("APPROVED"));
    const user = userEvent.setup();
    renderDetail([APPROVE]);

    await user.click(await screen.findByRole("button", { name: "Approve" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Approve" }));

    await waitFor(() => expect(approveRequisition).toHaveBeenCalledWith("req-1", {}));
    await waitFor(() => expect(getRequisition).toHaveBeenCalledTimes(2));
  });

  it("will not reject without a reason, and sends the reason once given", async () => {
    getRequisition.mockResolvedValue(requisition("SUBMITTED"));
    rejectRequisition.mockResolvedValue(requisition("REJECTED"));
    const user = userEvent.setup();
    renderDetail([APPROVE]);

    await user.click(await screen.findByRole("button", { name: "Reject" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Reject requisition" }));
    expect(await within(dialog).findByText("Say why this requisition is being rejected.")).toBeDefined();
    expect(rejectRequisition).not.toHaveBeenCalled();

    await user.type(within(dialog).getByRole("textbox", { name: /Reason/ }), "Over budget");
    await user.click(within(dialog).getByRole("button", { name: "Reject requisition" }));
    await waitFor(() => expect(rejectRequisition).toHaveBeenCalledWith("req-1", { reason: "Over budget" }));
  });

  it("disables Convert, and says why, for a buyer who cannot write purchase orders", async () => {
    getRequisition.mockResolvedValue(requisition("APPROVED"));
    renderDetail([WRITE]);

    const convert = await screen.findByRole("button", { name: "Convert to purchase order" });
    expect((convert as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText("Converting also needs permission to write purchase orders.")).toBeDefined();
  });

  it("does not offer Convert on a requisition that is not approved", async () => {
    getRequisition.mockResolvedValue(requisition("SUBMITTED"));
    renderDetail([WRITE, PO_WRITE, APPROVE]);
    await screen.findByRole("heading", { name: "PR-0001" });
    expect(names()).not.toContain("Convert to purchase order");
  });

  it("converts with the supplier, date and price overrides, then opens the new purchase order", async () => {
    getRequisition.mockResolvedValue(requisition("APPROVED"));
    convertRequisition.mockResolvedValue({ id: "po-7", po_number: "PO-0007" });
    const user = userEvent.setup();
    const router = renderDetail([WRITE, PO_WRITE]);

    await user.click(await screen.findByRole("button", { name: "Convert to purchase order" }));
    const dialog = await screen.findByRole("dialog");

    await user.type(within(dialog).getByRole("combobox", { name: "Supplier" }), "Kave");
    await waitFor(() => expect(screen.getByRole("option", { name: /Kaveri Textiles/ })).toBeDefined());
    await user.keyboard("{ArrowDown}{Enter}");
    await user.type(within(dialog).getByLabelText(/Expected date/), "2099-01-15");
    const price = within(dialog).getByRole("textbox", { name: /Unit price: FAB-1/ });
    await user.clear(price);
    await user.type(price, "26.50");
    await user.click(within(dialog).getByRole("button", { name: "Create purchase order" }));

    await waitFor(() => expect(convertRequisition).toHaveBeenCalledTimes(1));
    expect(convertRequisition).toHaveBeenCalledWith("req-1", {
      supplier_id: "sup-1",
      expected_date: "2099-01-15",
      warehouse: "Bhiwandi DC",
      currency: "INR",
      prices: [{ line_id: "line-1", unit_price_minor: 2650 }],
    });
    await waitFor(() => expect(router.state.location.pathname).toBe("/purchase-orders/po-7"));
  });
});
