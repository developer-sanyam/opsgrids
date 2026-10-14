/**
 * The purchase order screen's receiving additions: "Receive goods" appears for an approved or
 * partially received order to someone holding both receiving permissions, and the order lists the
 * deliveries posted against it.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { PoStatus, PurchaseOrderOut } from "@/api/types";
import { renderRoute } from "@/test-utils/renderRoute";
import { PurchaseOrderDetailPage } from "./PurchaseOrderDetail";

const getPurchaseOrder = vi.fn();
const listInventory = vi.fn();
const listGoodsReceipts = vi.fn();

vi.mock("@/api/client", () => ({
  getPurchaseOrder: (...args: unknown[]) => getPurchaseOrder(...args),
  listInventory: (...args: unknown[]) => listInventory(...args),
  listGoodsReceipts: (...args: unknown[]) => listGoodsReceipts(...args),
  approvePurchaseOrder: vi.fn(),
  cancelPurchaseOrder: vi.fn(),
  closePurchaseOrder: vi.fn(),
  deletePurchaseOrder: vi.fn(),
  receivePurchaseOrder: vi.fn(),
}));

const RECEIPT_WRITE = "operations.procurement.receipt.write:ORG";
const STOCK_RECEIVE = "operations.inventory.stock.receive:ORG";

function order(status: PoStatus): PurchaseOrderOut {
  return {
    id: "po-1",
    po_number: "PO-0001",
    supplier_id: "sup-1",
    supplier_name: "Kaveri Textiles",
    category: "raw_materials",
    status,
    currency: "INR",
    warehouse: "Bhiwandi DC",
    raised_by: "Asha Rao",
    created_date: "2026-09-25",
    expected_date: "2026-10-20",
    received_date: null,
    notes: null,
    line_items: [
      {
        id: "pol-1",
        sku: "FAB-100",
        description: "Cotton poplin",
        quantity: 100,
        uom: "m",
        unit_price_minor: 24000,
        received_qty: 20,
        received_exact: "20.5",
        short_closed: false,
      },
    ],
  };
}

function renderDetail(grants: string[]) {
  return renderRoute(
    [
      { path: "/purchase-orders/:id", element: <PurchaseOrderDetailPage /> },
      { path: "/purchase-orders/:id/receive", element: <h1>Receive form</h1> },
    ],
    "/purchase-orders/po-1",
    grants,
  );
}

describe("PurchaseOrderDetailPage receiving", () => {
  beforeEach(() => {
    getPurchaseOrder.mockReset();
    listInventory.mockResolvedValue({ items: [], total: 0, page: 1, size: 0, pages: 0 });
    listGoodsReceipts.mockResolvedValue({ items: [], next_cursor: null });
  });

  it("offers Receive goods on an approved order to someone with both permissions, and opens the form", async () => {
    getPurchaseOrder.mockResolvedValue(order("approved"));
    const user = userEvent.setup();
    const router = renderDetail([RECEIPT_WRITE, STOCK_RECEIVE]);

    await user.click(await screen.findByRole("button", { name: "Receive goods" }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/purchase-orders/po-1/receive"));
  });

  it("keeps Receive goods in a bar above the bottom navigation on a phone, exactly once", async () => {
    vi.stubGlobal("matchMedia", (query: string) => ({
      matches: false,
      media: query,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
    }));
    try {
      getPurchaseOrder.mockResolvedValue(order("approved"));
      renderDetail([RECEIPT_WRITE, STOCK_RECEIVE]);

      const button = await screen.findByRole("button", { name: "Receive goods" });
      expect(screen.getAllByRole("button", { name: "Receive goods" })).toHaveLength(1);
      const bar = button.closest("div.sticky");
      expect(bar).not.toBeNull();
      expect((bar as HTMLElement).style.bottom).toBe("var(--shell-bottom-inset)");
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("hides Receive goods from someone missing either permission", async () => {
    getPurchaseOrder.mockResolvedValue(order("approved"));
    renderDetail([RECEIPT_WRITE]);

    await screen.findByRole("heading", { name: "PO-0001" });
    expect(screen.queryByRole("button", { name: "Receive goods" })).toBeNull();
  });

  it("does not offer Receive goods on a draft, and lists no receipts for it", async () => {
    getPurchaseOrder.mockResolvedValue(order("draft"));
    renderDetail([RECEIPT_WRITE, STOCK_RECEIVE]);

    await screen.findByRole("heading", { name: "PO-0001" });
    expect(screen.queryByRole("button", { name: "Receive goods" })).toBeNull();
    expect(screen.queryByText("Goods receipts")).toBeNull();
  });

  it("shows a partially received order's progress and the receipts posted against it", async () => {
    getPurchaseOrder.mockResolvedValue(order("partially_received"));
    listGoodsReceipts.mockResolvedValue({
      items: [
        {
          id: "grn-1",
          number: "GRN-0001",
          purchase_order_id: "po-1",
          po_number: "PO-0001",
          supplier_name: "Kaveri Textiles",
          received_on: "2026-10-04",
          delivery_note: "DN-77",
          line_count: 1,
          created_at: "2026-10-04T10:00:00Z",
        },
      ],
      next_cursor: null,
    });
    renderDetail([RECEIPT_WRITE, STOCK_RECEIVE]);

    expect(await screen.findByText("20.5 received")).toBeDefined();
    expect(screen.getAllByText("Partially received").length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "Receive goods" })).toBeDefined();
    const link = await screen.findByRole("link", { name: /GRN-0001/ });
    expect(link.getAttribute("href")).toBe("/goods-receipts/grn-1");
    expect(listGoodsReceipts).toHaveBeenCalledWith(expect.objectContaining({ purchase_order_id: "po-1" }));
  });

  it("says nothing has been received yet for an approved order with no receipts", async () => {
    getPurchaseOrder.mockResolvedValue(order("approved"));
    renderDetail([]);
    expect(await screen.findByText("Nothing received yet")).toBeDefined();
  });
});
