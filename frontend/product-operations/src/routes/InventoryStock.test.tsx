/**
 * The stock screen: all four states of the async surface (loading, error, empty, populated), the
 * cursor "Load more", and the permission shape — a caller without `stock.read` sees why, one with
 * only read sees rows without write controls, and each write control follows its own grant.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiError } from "@opsgrids/client";
import { renderScreen } from "@/test-utils/renderScreen";
import { item, stockRow } from "@/test-utils/tracking";
import { InventoryStockPage } from "./InventoryStock";

const listStock = vi.fn();
const locateStock = vi.fn();
const getInventoryItem = vi.fn();

vi.mock("@/api/tracking", () => ({
  listStock: (...args: unknown[]) => listStock(...args),
  locateStock: (...args: unknown[]) => locateStock(...args),
  listLots: () => Promise.resolve({ items: [], next_cursor: null }),
  listRolls: () => Promise.resolve({ items: [], next_cursor: null }),
  transferStock: () => Promise.reject(new Error("not used")),
  changeStockStatus: () => Promise.reject(new Error("not used")),
  postOpeningStock: () => Promise.reject(new Error("not used")),
}));
vi.mock("@/api/client", () => ({
  FULL_PAGE: 200,
  listWarehouses: () => Promise.resolve({ items: [], total: 0, page: 1, size: 200, pages: 0 }),
  listLocations: () => Promise.resolve([]),
  searchInventory: () => Promise.resolve([]),
  getInventoryItem: (...args: unknown[]) => getInventoryItem(...args),
}));

const READ = "operations.inventory.stock.read:ORG";
const TRANSFER = "operations.inventory.stock.transfer:ORG";
const ADJUST = "operations.inventory.stock.adjust:ORG";

describe("InventoryStockPage", () => {
  beforeEach(() => {
    listStock.mockReset();
  });

  it("says what is missing instead of showing an empty page, and reads nothing, without stock.read", async () => {
    renderScreen(<InventoryStockPage />, []);
    expect(await screen.findByText("You don't have access to stock")).toBeDefined();
    expect(listStock).not.toHaveBeenCalled();
  });

  it("shows a loading state that holds the table's place", async () => {
    listStock.mockReturnValue(new Promise(() => undefined));
    renderScreen(<InventoryStockPage />, [READ]);
    await screen.findByRole("heading", { name: "Stock" });
    expect(document.querySelector("[aria-busy='true']")).not.toBeNull();
  });

  it("shows the error with a retry that asks again", async () => {
    const user = userEvent.setup();
    listStock.mockRejectedValueOnce(new ApiError(500, "The ledger is unavailable", { detail: "The ledger is unavailable" }));
    listStock.mockResolvedValueOnce({ items: [stockRow()], next_cursor: null });
    renderScreen(<InventoryStockPage />, [READ]);

    expect(await screen.findByText("The ledger is unavailable")).toBeDefined();
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("A-01")).toBeDefined();
  });

  it("explains an empty book and offers opening stock to someone who can post it", async () => {
    listStock.mockResolvedValue({ items: [], next_cursor: null });
    renderScreen(<InventoryStockPage />, [READ, ADJUST]);

    expect(await screen.findByText("No stock on the books yet")).toBeDefined();
    expect(screen.getAllByRole("button", { name: /opening stock/i }).length).toBeGreaterThan(0);
  });

  it("does not offer opening stock to a reader", async () => {
    listStock.mockResolvedValue({ items: [], next_cursor: null });
    renderScreen(<InventoryStockPage />, [READ]);

    await screen.findByText("No stock on the books yet");
    expect(screen.queryByRole("button", { name: /opening stock/i })).toBeNull();
  });

  it("shows rows with exact quantities and no write controls to a reader", async () => {
    listStock.mockResolvedValue({
      items: [stockRow({ on_hand_qty: "82.5", reserved_qty: "10", available_qty: "72.5" })],
      next_cursor: null,
    });
    renderScreen(<InventoryStockPage />, [READ]);

    expect(await screen.findByText("A-01")).toBeDefined();
    expect(screen.getAllByText("82.5").length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: /Actions for/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /more actions|actions/i })).toBeNull();
  });

  it("offers row actions to someone who can transfer", async () => {
    listStock.mockResolvedValue({ items: [stockRow()], next_cursor: null });
    renderScreen(<InventoryStockPage />, [READ, TRANSFER]);

    await screen.findByText("A-01");
    expect(screen.getAllByRole("button", { name: /FAB-100 at A-01/ }).length).toBeGreaterThan(0);
  });

  it("pages with the server's cursor: Load more appends, and the footer only claims all rows at the end", async () => {
    const user = userEvent.setup();
    listStock.mockResolvedValueOnce({
      items: [stockRow({ id: "b1", location_code: "A-01" })],
      next_cursor: "cursor-2",
    });
    listStock.mockResolvedValueOnce({
      items: [stockRow({ id: "b2", location_code: "B-02" })],
      next_cursor: null,
    });
    renderScreen(<InventoryStockPage />, [READ]);

    expect(await screen.findByText("Showing 1 stock rows")).toBeDefined();
    await user.click(screen.getByRole("button", { name: "Load more" }));

    expect(await screen.findByText("Showing all 2 stock rows")).toBeDefined();
    expect(screen.getByText("B-02")).toBeDefined();
    expect(listStock).toHaveBeenLastCalledWith(expect.objectContaining({ cursor: "cursor-2" }));
    expect(screen.queryByRole("button", { name: "Load more" })).toBeNull();
  });

  it("keeps the rows and offers a retry when Load more fails", async () => {
    const user = userEvent.setup();
    listStock.mockResolvedValueOnce({ items: [stockRow({ id: "b1" })], next_cursor: "cursor-2" });
    listStock.mockRejectedValueOnce(new ApiError(504, "Timed out", { detail: "Timed out" }));
    renderScreen(<InventoryStockPage />, [READ]);

    await screen.findByText("Showing 1 stock rows");
    await user.click(screen.getByRole("button", { name: "Load more" }));

    expect(await screen.findByText("Timed out")).toBeDefined();
    expect(screen.getByText("A-01")).toBeDefined();
    await waitFor(() => expect(screen.getByRole("button", { name: "Try again" })).toBeDefined());
  });

  describe("Find stock", () => {
    beforeEach(() => {
      listStock.mockResolvedValue({ items: [], next_cursor: null });
      locateStock.mockReset();
      getInventoryItem.mockReset();
    });

    it("sits beside the filters, waiting for an item, and asks nothing until then", async () => {
      renderScreen(<InventoryStockPage />, [READ]);
      expect(await screen.findByText("Find stock")).toBeDefined();
      expect((screen.getByRole("button", { name: "Find where it is" }) as HTMLButtonElement).disabled).toBe(true);
      expect(screen.getByText("Choose an item above to see which bins hold it.")).toBeDefined();
      expect(locateStock).not.toHaveBeenCalled();
    });

    it("searches for the item the page is narrowed to, with the lot and roll from the address", async () => {
      const user = userEvent.setup();
      getInventoryItem.mockResolvedValue(item({ id: "item-1", sku: "FAB-100", uom: "m" }));
      locateStock.mockResolvedValue({
        results: [
          {
            warehouse_id: "wh-1",
            warehouse_name: "Bhiwandi DC",
            floor_id: "floor-1",
            floor_name: "Ground",
            location_id: "loc-1",
            code: "A-01",
            name: "Bin A-01",
            kind: "bin",
            path: "Zone A / A-01",
            quantity: "40",
            status: "AVAILABLE",
            placement: {
              layout_version_id: "lv-1",
              x_mm: 0,
              y_mm: 0,
              z_mm: null,
              width_mm: 800,
              depth_mm: 600,
              height_mm: null,
              source: "own",
              ancestor_location_id: null,
            },
            placement_reason: null,
          },
        ],
      });
      renderScreen(<InventoryStockPage />, [READ], {
        path: "/inventory/stock",
        at: "/inventory/stock?item=item-1&lot=lot-1&roll=roll-1",
      });

      await user.click(await screen.findByRole("button", { name: "Find where it is" }));
      expect(locateStock).toHaveBeenCalledWith({ inventory_item_id: "item-1", lot_id: "lot-1", roll_id: "roll-1" });
      expect(await screen.findByRole("link", { name: "Show on floor for A-01" })).toBeDefined();
    });

    it("keeps a failed lookup inside its own card: the stock table beside it still loads", async () => {
      const user = userEvent.setup();
      getInventoryItem.mockResolvedValue(item({ id: "item-1" }));
      listStock.mockResolvedValue({ items: [stockRow()], next_cursor: null });
      locateStock.mockRejectedValue(new ApiError(500, "Locate is unavailable", { detail: "Locate is unavailable" }));
      renderScreen(<InventoryStockPage />, [READ], { path: "/inventory/stock", at: "/inventory/stock?item=item-1" });

      await user.click(await screen.findByRole("button", { name: "Find where it is" }));
      expect(await screen.findByText("Locate is unavailable")).toBeDefined();
      expect(await screen.findByText("A-01")).toBeDefined();
    });
  });
});
