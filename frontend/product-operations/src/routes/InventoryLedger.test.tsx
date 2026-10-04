/**
 * The ledger screen: every filter the server takes (item, location, lot, roll, kind, date range)
 * reaches the request, a link from a lot or roll lands already narrowed, the four states of the
 * surface exist, and nothing on it edits a row.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiError } from "@opsgrids/client";
import type { LedgerRowOut } from "@/api/trackingTypes";
import { renderScreen } from "@/test-utils/renderScreen";
import { item } from "@/test-utils/tracking";
import { InventoryLedgerPage } from "./InventoryLedger";

const listLedger = vi.fn();
const getInventoryItem = vi.fn();

vi.mock("@/api/tracking", () => ({
  listLedger: (...args: unknown[]) => listLedger(...args),
  listLots: () => Promise.resolve({ items: [], next_cursor: null }),
  listRolls: () => Promise.resolve({ items: [], next_cursor: null }),
}));
vi.mock("@/api/client", () => ({
  FULL_PAGE: 200,
  listWarehouses: () =>
    Promise.resolve({ items: [{ id: "wh-1", name: "Bhiwandi DC" }], total: 1, page: 1, size: 200, pages: 1 }),
  listLocations: () => Promise.resolve([{ id: "loc-1", code: "A-01", name: "A-01" }]),
  searchInventory: () => Promise.resolve([]),
  getInventoryItem: (...args: unknown[]) => getInventoryItem(...args),
}));

const READ = "operations.inventory.stock.read:ORG";

function row(overrides: Partial<LedgerRowOut> = {}): LedgerRowOut {
  return {
    id: "led-1",
    move_id: "move-1",
    kind: "relocation",
    reason: null,
    reference_type: "roll_split",
    reference_id: null,
    note: "",
    occurred_on: "2026-10-02",
    inventory_item_id: "item-1",
    sku: "FAB-100",
    uom: "m",
    location_id: "loc-1",
    location_code: "A-01",
    status: "AVAILABLE",
    lot_id: "lot-1",
    roll_id: "roll-1",
    quantity: "-82.5",
    balance_after: "0",
    created_at: "2026-10-02T10:00:00Z",
    created_by: null,
    ...overrides,
  };
}

describe("InventoryLedgerPage", () => {
  beforeEach(() => {
    listLedger.mockReset();
    getInventoryItem.mockReset();
    listLedger.mockResolvedValue({ items: [row()], next_cursor: null });
  });

  it("explains a missing grant", async () => {
    renderScreen(<InventoryLedgerPage />, []);
    expect(await screen.findByText("You don't have access to the stock ledger")).toBeDefined();
    expect(listLedger).not.toHaveBeenCalled();
  });

  it("names the operation behind a relocation, and shows the signed change", async () => {
    renderScreen(<InventoryLedgerPage />, [READ]);
    expect(await screen.findByText("Roll split")).toBeDefined();
    expect(screen.getByText("-82.5")).toBeDefined();
  });

  it("has no control that edits or deletes a row", async () => {
    renderScreen(<InventoryLedgerPage />, [READ]);
    await screen.findByText("Roll split");
    expect(screen.queryByRole("button", { name: /edit|delete|remove/i })).toBeNull();
  });

  it("sends the kind and the date range to the server", async () => {
    const user = userEvent.setup();
    renderScreen(<InventoryLedgerPage />, [READ]);
    await screen.findByText("Roll split");

    await user.selectOptions(screen.getByLabelText("Kind"), "opening");
    await user.type(screen.getByLabelText("From"), "2026-10-01");
    await user.type(screen.getByLabelText("To"), "2026-10-05");

    await waitFor(() =>
      expect(listLedger).toHaveBeenLastCalledWith(
        expect.objectContaining({ kind: "opening", from_date: "2026-10-01", to_date: "2026-10-05" }),
      ),
    );
  });

  it("sends the location once a warehouse is chosen", async () => {
    const user = userEvent.setup();
    renderScreen(<InventoryLedgerPage />, [READ]);
    await screen.findByText("Roll split");

    expect(screen.getByLabelText("Location")).toHaveProperty("disabled", true);
    await screen.findByRole("option", { name: "Bhiwandi DC" });
    await user.selectOptions(screen.getByLabelText("Warehouse"), "wh-1");
    await screen.findByRole("option", { name: "A-01" });
    await user.selectOptions(screen.getByLabelText("Location"), "loc-1");

    await waitFor(() => expect(listLedger).toHaveBeenLastCalledWith(expect.objectContaining({ location_id: "loc-1" })));
  });

  it("lands already narrowed from a link: item, lot and roll in the address reach the request", async () => {
    getInventoryItem.mockResolvedValue(item());
    renderScreen(<InventoryLedgerPage />, [READ], {
      path: "/inventory/ledger",
      at: "/inventory/ledger?item=item-1&lot=lot-1&roll=roll-1",
    });

    await waitFor(() =>
      expect(listLedger).toHaveBeenLastCalledWith(
        expect.objectContaining({ item_id: "item-1", lot_id: "lot-1", roll_id: "roll-1" }),
      ),
    );
    expect(await screen.findByRole("button", { name: "Clear filters" })).toBeDefined();
  });

  it("explains an empty ledger differently from an empty result", async () => {
    const user = userEvent.setup();
    listLedger.mockResolvedValue({ items: [], next_cursor: null });
    renderScreen(<InventoryLedgerPage />, [READ]);
    expect(await screen.findByText("The ledger is empty")).toBeDefined();

    await user.selectOptions(screen.getByLabelText("Kind"), "issue");
    expect(await screen.findByText("No ledger rows match these filters")).toBeDefined();
  });

  it("shows an error with a retry", async () => {
    const user = userEvent.setup();
    listLedger.mockRejectedValueOnce(new ApiError(500, "Ledger unavailable", { detail: "Ledger unavailable" }));
    renderScreen(<InventoryLedgerPage />, [READ]);

    expect(await screen.findByText("Ledger unavailable")).toBeDefined();
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("Roll split")).toBeDefined();
  });

  it("pages on the server's cursor", async () => {
    const user = userEvent.setup();
    listLedger.mockResolvedValueOnce({ items: [row({ id: "l1" })], next_cursor: "next-1" });
    listLedger.mockResolvedValueOnce({ items: [row({ id: "l2", kind: "opening", reference_type: null, quantity: "82.5" })], next_cursor: null });
    renderScreen(<InventoryLedgerPage />, [READ]);

    await screen.findByText("Showing 1 ledger rows");
    await user.click(screen.getByRole("button", { name: "Load more" }));

    expect(await screen.findByText("Showing all 2 ledger rows")).toBeDefined();
    expect(listLedger).toHaveBeenLastCalledWith(expect.objectContaining({ cursor: "next-1" }));
  });
});
