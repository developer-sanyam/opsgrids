/**
 * The goods receipt list is keyset-paginated: "Load more" appends the next page under the rows
 * already shown, a failed page keeps them, and every async state has a screen.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiError } from "@opsgrids/client";

import type { CursorPage } from "@/api/trackingTypes";
import type { GoodsReceiptSummaryOut } from "@/api/types";
import { renderRoute } from "@/test-utils/renderRoute";
import { GoodsReceiptsPage } from "./GoodsReceipts";

const listGoodsReceipts = vi.fn();

vi.mock("@/api/client", () => ({
  listGoodsReceipts: (...args: unknown[]) => listGoodsReceipts(...args),
}));

function row(n: number): GoodsReceiptSummaryOut {
  return {
    id: `grn-${n}`,
    number: `GRN-000${n}`,
    purchase_order_id: "po-1",
    po_number: "PO-0001",
    supplier_name: "Kaveri Textiles",
    received_on: "2026-10-04",
    delivery_note: null,
    line_count: 2,
    created_at: "2026-10-04T10:00:00Z",
  };
}

function page(items: GoodsReceiptSummaryOut[], next: string | null): CursorPage<GoodsReceiptSummaryOut> {
  return { items, next_cursor: next };
}

function renderList(entry = "/goods-receipts") {
  return renderRoute(
    [
      { path: "/goods-receipts", element: <GoodsReceiptsPage /> },
      { path: "/goods-receipts/:id", element: <h1>Receipt detail</h1> },
    ],
    entry,
    [],
  );
}

describe("GoodsReceiptsPage", () => {
  beforeEach(() => {
    listGoodsReceipts.mockReset();
  });

  it("explains an empty list", async () => {
    listGoodsReceipts.mockResolvedValue(page([], null));
    renderList();
    expect(await screen.findByText("No goods receipts yet")).toBeDefined();
  });

  it("loads more by cursor, appending under the rows already shown", async () => {
    listGoodsReceipts
      .mockResolvedValueOnce(page([row(1), row(2)], "cursor-2"))
      .mockResolvedValueOnce(page([row(3)], null));
    const user = userEvent.setup();
    renderList();

    expect(await screen.findByText("GRN-0001")).toBeDefined();
    expect(screen.getByText("2 receipts shown")).toBeDefined();
    await user.click(screen.getByRole("button", { name: "Load more" }));

    expect(await screen.findByText("GRN-0003")).toBeDefined();
    expect(screen.getByText("GRN-0001")).toBeDefined();
    expect(listGoodsReceipts).toHaveBeenLastCalledWith(expect.objectContaining({ cursor: "cursor-2" }));
    expect(screen.queryByRole("button", { name: "Load more" })).toBeNull();
    expect(screen.getByText("3 receipts shown, that's all of them")).toBeDefined();
  });

  it("keeps the rows when loading more fails, and offers a retry", async () => {
    listGoodsReceipts
      .mockResolvedValueOnce(page([row(1)], "cursor-2"))
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValueOnce(page([row(2)], null));
    const user = userEvent.setup();
    renderList();

    await user.click(await screen.findByRole("button", { name: "Load more" }));
    expect(await screen.findByText("Couldn't load more receipts. The ones above are unchanged.")).toBeDefined();
    expect(screen.getByText("GRN-0001")).toBeDefined();

    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("GRN-0002")).toBeDefined();
  });

  it("narrows to one purchase order from the URL, and can be cleared", async () => {
    listGoodsReceipts.mockResolvedValue(page([row(1)], null));
    const user = userEvent.setup();
    renderList("/goods-receipts?purchase_order_id=po-1");

    await screen.findByText("GRN-0001");
    expect(listGoodsReceipts).toHaveBeenCalledWith(expect.objectContaining({ purchase_order_id: "po-1" }));

    await user.click(screen.getByRole("button", { name: "Show all receipts" }));
    await waitFor(() =>
      expect(listGoodsReceipts).toHaveBeenLastCalledWith(expect.not.objectContaining({ purchase_order_id: "po-1" })),
    );
  });

  it("shows the server's problem with a retry", async () => {
    listGoodsReceipts.mockRejectedValue(new ApiError(500, "x", { title: "Server error", status: 500, detail: "The receipt service is down." }));
    renderList();
    expect(await screen.findByText("The receipt service is down.")).toBeDefined();
  });
});
