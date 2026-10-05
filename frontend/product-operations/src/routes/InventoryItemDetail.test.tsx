/**
 * Item detail's tabs: Overview, Locations, Lots, Rolls, Ledger. Each panel loads only when opened,
 * is keyboard-operable (a real tablist), and says what an untracked item can't have.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { renderScreen } from "@/test-utils/renderScreen";
import { item, lot, roll, stockRow } from "@/test-utils/tracking";
import { InventoryItemDetailPage } from "./InventoryItemDetail";

const getInventoryItem = vi.fn();
const listStock = vi.fn();
const listLots = vi.fn();
const listRolls = vi.fn();
const listLedger = vi.fn();
const locateStock = vi.fn();

vi.mock("@/api/client", () => ({
  FULL_PAGE: 200,
  getInventoryItem: (...args: unknown[]) => getInventoryItem(...args),
  listMovements: () => Promise.resolve({ items: [], total: 0, page: 1, size: 25, pages: 0 }),
  listItemAttributes: () => Promise.resolve([]),
  listItemVariants: () => Promise.resolve([]),
  listWarehouses: () => Promise.resolve({ items: [], total: 0, page: 1, size: 200, pages: 0 }),
  listLocations: () => Promise.resolve([]),
  searchInventory: () => Promise.resolve([]),
  listWorkers: () => Promise.resolve({ items: [], total: 0, page: 1, size: 200, pages: 0 }),
}));
vi.mock("@/api/tracking", () => ({
  listStock: (...args: unknown[]) => listStock(...args),
  listLots: (...args: unknown[]) => listLots(...args),
  listRolls: (...args: unknown[]) => listRolls(...args),
  listLedger: (...args: unknown[]) => listLedger(...args),
  locateStock: (...args: unknown[]) => locateStock(...args),
  getPutawayDetail: () => Promise.reject(new Error("not used")),
  revealPutawayLocation: () => Promise.reject(new Error("not used")),
  transferStock: () => Promise.reject(new Error("not used")),
  changeStockStatus: () => Promise.reject(new Error("not used")),
  postOpeningStock: () => Promise.reject(new Error("not used")),
  createLot: () => Promise.reject(new Error("not used")),
  createRoll: () => Promise.reject(new Error("not used")),
}));

const READ = "operations.inventory.stock.read:ORG";
const LOT_WRITE = "operations.inventory.lot.write:ORG";
const ADJUST = "operations.inventory.stock.adjust:ORG";

function renderDetail(grants: string[], at = "/inventory/item-1") {
  return renderScreen(<InventoryItemDetailPage />, grants, { path: "/inventory/:id", at });
}

describe("InventoryItemDetailPage tabs", () => {
  beforeEach(() => {
    getInventoryItem.mockReset();
    listStock.mockReset();
    listLots.mockReset();
    listRolls.mockReset();
    listLedger.mockReset();
    getInventoryItem.mockResolvedValue(item({ track_lot: true, track_roll: true, on_hand_qty: "82.5" }));
    listStock.mockResolvedValue({ items: [stockRow()], next_cursor: null });
    listLots.mockResolvedValue({ items: [lot({ lot_no: "LOT-1" })], next_cursor: null });
    listRolls.mockResolvedValue({ items: [roll({ roll_no: "R-001" })], next_cursor: null });
    listLedger.mockResolvedValue({ items: [], next_cursor: null });
  });

  it("has the five tabs in one tab list, Overview selected, and loads no tab's data until it is opened", async () => {
    renderDetail([READ]);
    const tabs = await screen.findAllByRole("tab");
    expect(tabs.map((t) => t.textContent)).toEqual(["Overview", "Locations", "Lots", "Rolls", "Ledger"]);
    expect(screen.getByRole("tab", { name: "Overview" }).getAttribute("aria-selected")).toBe("true");
    expect(listStock).not.toHaveBeenCalled();
    expect(listLots).not.toHaveBeenCalled();
    expect(listRolls).not.toHaveBeenCalled();
    expect(listLedger).not.toHaveBeenCalled();
  });

  it("opens Locations with the item's stock", async () => {
    const user = userEvent.setup();
    renderDetail([READ]);
    await user.click(await screen.findByRole("tab", { name: "Locations" }));

    expect(await screen.findByText("A-01")).toBeDefined();
    expect(listStock).toHaveBeenCalledWith(expect.objectContaining({ item_id: "item-1" }));
  });

  it("puts Find stock on the Locations tab, for this item, asking only when clicked", async () => {
    const user = userEvent.setup();
    locateStock.mockResolvedValue({ results: [] });
    renderDetail([READ]);
    await user.click(await screen.findByRole("tab", { name: "Locations" }));

    await screen.findByText("A-01");
    expect(locateStock).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Find where it is" }));
    expect(locateStock).toHaveBeenCalledWith({ inventory_item_id: "item-1", lot_id: undefined, roll_id: undefined });
  });

  it("opens Lots and Rolls for the item, and Ledger with its slice of the ledger", async () => {
    const user = userEvent.setup();
    renderDetail([READ]);
    await user.click(await screen.findByRole("tab", { name: "Lots" }));
    expect(await screen.findByRole("link", { name: "LOT-1" })).toBeDefined();
    expect(listLots).toHaveBeenCalledWith(expect.objectContaining({ item_id: "item-1" }));

    await user.click(screen.getByRole("tab", { name: "Rolls" }));
    expect(await screen.findByRole("link", { name: "R-001" })).toBeDefined();

    await user.click(screen.getByRole("tab", { name: "Ledger" }));
    expect(await screen.findByText("The ledger is empty")).toBeDefined();
    expect(listLedger).toHaveBeenCalledWith(expect.objectContaining({ item_id: "item-1" }));
  });

  it("moves between tabs with the arrow keys, and opens the focused one on Enter", async () => {
    const user = userEvent.setup();
    renderDetail([READ]);
    const overview = await screen.findByRole("tab", { name: "Overview" });
    overview.focus();

    await user.keyboard("{ArrowRight}");
    expect(document.activeElement).toBe(screen.getByRole("tab", { name: "Locations" }));
    await user.keyboard("{Enter}");
    expect(await screen.findByText("A-01")).toBeDefined();
    expect(screen.getByRole("tab", { name: "Locations" }).getAttribute("aria-selected")).toBe("true");
  });

  it("says an item that is not lot-tracked can't have lots, instead of an empty table", async () => {
    getInventoryItem.mockResolvedValue(item({ track_lot: false }));
    const user = userEvent.setup();
    renderDetail([READ]);
    await user.click(await screen.findByRole("tab", { name: "Lots" }));

    expect(await screen.findByText("This item isn't tracked by lot")).toBeDefined();
    expect(listLots).not.toHaveBeenCalledWith(expect.objectContaining({ item_id: "item-1" }));
  });

  it("offers Create lot to a lot writer, and not to a reader", async () => {
    const user = userEvent.setup();
    const { unmount } = renderDetail([READ, LOT_WRITE]);
    await user.click(await screen.findByRole("tab", { name: "Lots" }));
    expect((await screen.findAllByRole("button", { name: "Create lot" })).length).toBeGreaterThan(0);
    unmount();

    renderDetail([READ]);
    await user.click(await screen.findByRole("tab", { name: "Lots" }));
    await screen.findByRole("link", { name: "LOT-1" });
    expect(screen.queryByRole("button", { name: "Create lot" })).toBeNull();
  });

  it("explains a tab the caller can't read, instead of an empty panel", async () => {
    const user = userEvent.setup();
    renderDetail([]);
    await user.click(await screen.findByRole("tab", { name: "Ledger" }));
    expect(await screen.findByText("You don't have access to this item's ledger")).toBeDefined();
    expect(listLedger).not.toHaveBeenCalled();
  });

  it("opens straight onto a tab from the address", async () => {
    renderDetail([READ], "/inventory/item-1?tab=ledger");
    expect(await screen.findByText("The ledger is empty")).toBeDefined();
    expect(screen.getByRole("tab", { name: "Ledger" }).getAttribute("aria-selected")).toBe("true");
  });

  it("gives Opening stock only to someone who can adjust stock", async () => {
    const { unmount } = renderDetail([READ, ADJUST]);
    expect(await screen.findByRole("button", { name: "Opening stock" })).toBeDefined();
    unmount();

    renderDetail([READ]);
    await screen.findAllByRole("tab");
    expect(screen.queryByRole("button", { name: "Opening stock" })).toBeNull();
  });
});
