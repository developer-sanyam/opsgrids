/**
 * The goods receipt detail: every line's ordered / previously received / this receipt / cumulative
 * figures, its outcome as a word, the stock status it landed in, and its lot and rolls.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, within } from "@testing-library/react";

import { ApiError } from "@opsgrids/client";

import type { GoodsReceiptLineOut, GoodsReceiptOut } from "@/api/types";
import { renderRoute } from "@/test-utils/renderRoute";
import { GoodsReceiptDetailPage } from "./GoodsReceiptDetail";

const getGoodsReceipt = vi.fn();

vi.mock("@/api/client", () => ({
  getGoodsReceipt: (...args: unknown[]) => getGoodsReceipt(...args),
}));

function line(overrides: Partial<GoodsReceiptLineOut> = {}): GoodsReceiptLineOut {
  return {
    id: "gl-1",
    line_no: 1,
    purchase_order_line_id: "pol-1",
    sku: "FAB-100",
    description: "Cotton poplin",
    inventory_item_id: "item-1",
    stock_uom: "m",
    location_id: "loc-1",
    location_label: "REC-1 · Receiving bay",
    stock_status: "QUALITY_HOLD",
    lot_id: "lot-1",
    lot_no: "LOT-2026-001",
    supplier_lot_no: "KT-77",
    rolls: [
      { id: "r1", roll_no: "R-001", primary_qty: "50", secondary_qty: "9.2", secondary_uom: "kg" },
      { id: "r2", roll_no: "R-002", primary_qty: "32.5", secondary_qty: null, secondary_uom: null },
    ],
    entered_qty: "82.5",
    entered_uom: "m",
    received_qty: "82.5",
    ordered_qty: "100",
    prior_received_qty: "20",
    cumulative_qty: "102.5",
    tolerance_pct: "2.5",
    close_short: false,
    outcome: "OVER",
    unit_cost_minor: 2450,
    unit_cost_currency: "INR",
    ...overrides,
  };
}

function receipt(lines: GoodsReceiptLineOut[], overrides: Partial<GoodsReceiptOut> = {}): GoodsReceiptOut {
  return {
    id: "grn-1",
    number: "GRN-0001",
    purchase_order_id: "po-1",
    po_number: "PO-0001",
    po_status: "partially_received",
    supplier_id: "sup-1",
    supplier_name: "Kaveri Textiles",
    received_on: "2026-10-04",
    delivery_note: "DN-77",
    note: null,
    status: "POSTED",
    replayed: false,
    created_at: "2026-10-04T10:00:00Z",
    created_by: "Asha Rao",
    lines,
    ...overrides,
  };
}

function renderDetail() {
  return renderRoute(
    [{ path: "/goods-receipts/:id", element: <GoodsReceiptDetailPage /> }],
    "/goods-receipts/grn-1",
    [],
  );
}

describe("GoodsReceiptDetailPage", () => {
  beforeEach(() => {
    getGoodsReceipt.mockReset();
  });

  it("shows the header, and per line the four quantities, the outcome, status and location", async () => {
    getGoodsReceipt.mockResolvedValue(receipt([line()]));
    renderDetail();

    expect(await screen.findByRole("heading", { name: "GRN-0001" })).toBeDefined();
    expect(screen.getByRole("link", { name: "PO-0001" }).getAttribute("href")).toBe("/purchase-orders/po-1");
    expect(screen.getByText("DN-77")).toBeDefined();

    const table = screen.getByRole("table", { name: "Lines received on GRN-0001" });
    const row = within(table).getAllByRole("row")[1] as HTMLElement;
    expect(within(row).getByText("100 m")).toBeDefined();
    expect(within(row).getByText("20 m")).toBeDefined();
    expect(within(row).getByText("82.5 m")).toBeDefined();
    expect(within(row).getByText("102.5 m")).toBeDefined();
    expect(within(row).getByText("Over")).toBeDefined();
    expect(within(row).getByText("Quality hold")).toBeDefined();
    expect(within(row).getByText("REC-1 · Receiving bay")).toBeDefined();
  });

  it("shows the lot and each roll's length and measured weight", async () => {
    getGoodsReceipt.mockResolvedValue(receipt([line()]));
    renderDetail();

    expect(await screen.findByText("Lot LOT-2026-001 (supplier lot KT-77)")).toBeDefined();
    const rolls = screen.getByRole("table", { name: "Rolls received on FAB-100" });
    expect(within(rolls).getByText("R-001")).toBeDefined();
    expect(within(rolls).getByText("50 m")).toBeDefined();
    expect(within(rolls).getByText("9.2 kg")).toBeDefined();
    expect(within(rolls).getByText("R-002")).toBeDefined();
    expect(within(rolls).getByText("32.5 m")).toBeDefined();
  });

  it("names each outcome as a word, and says what a container was entered as", async () => {
    getGoodsReceipt.mockResolvedValue(
      receipt([
        line({ id: "a", outcome: "PARTIAL", rolls: [], lot_no: null, stock_status: "AVAILABLE" }),
        line({ id: "b", outcome: "EXACT", rolls: [], lot_no: null }),
        line({ id: "c", outcome: "SHORT", close_short: true, rolls: [], lot_no: null, entered_qty: "7", entered_uom: "box", stock_uom: "pcs", received_qty: "84" }),
      ]),
    );
    renderDetail();

    await screen.findByRole("heading", { name: "GRN-0001" });
    expect(screen.getByText("Partial")).toBeDefined();
    expect(screen.getByText("Exact")).toBeDefined();
    expect(screen.getByText("Short")).toBeDefined();
    expect(screen.getByText("Closed short")).toBeDefined();
    expect(screen.getByText("entered as 7 box")).toBeDefined();
  });

  it("tells a retried posting it was already posted", async () => {
    getGoodsReceipt.mockResolvedValue(receipt([line({ rolls: [] })], { replayed: true }));
    renderDetail();
    expect(await screen.findByText("This receipt was already posted")).toBeDefined();
  });

  it("shows a loading state, then an error with a retry that works", async () => {
    getGoodsReceipt.mockRejectedValueOnce(new ApiError(500, "x", { title: "Server error", status: 500, detail: "The receipt service is down." }));
    renderDetail();
    expect(await screen.findByText("The receipt service is down.")).toBeDefined();
    expect(screen.getByRole("button", { name: "Try again" })).toBeDefined();
  });

  it("renders a 404 as not found, with no retry", async () => {
    getGoodsReceipt.mockRejectedValue(new ApiError(404, "x", { title: "Not found", status: 404, detail: "That goods receipt is not part of this organization" }));
    renderDetail();
    expect(await screen.findByText("Not found")).toBeDefined();
    expect(screen.queryByRole("button", { name: "Try again" })).toBeNull();
  });
});
