/**
 * The rolls list: filters reach the server, merge selection exists only for someone who can write
 * rolls, and the merge button waits for two rolls.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { renderScreen } from "@/test-utils/renderScreen";
import { roll, stockRow } from "@/test-utils/tracking";
import { InventoryRollsPage } from "./InventoryRolls";

const listRolls = vi.fn();
const listStock = vi.fn();

vi.mock("@/api/tracking", () => ({
  listRolls: (...args: unknown[]) => listRolls(...args),
  listStock: (...args: unknown[]) => listStock(...args),
  mergeRolls: () => Promise.reject(new Error("not used")),
  createRoll: () => Promise.reject(new Error("not used")),
  listLots: () => Promise.resolve({ items: [], next_cursor: null }),
}));
vi.mock("@/api/client", () => ({
  searchInventory: () => Promise.resolve([]),
}));

const READ = "operations.inventory.stock.read:ORG";
const WRITE = "operations.inventory.roll.write:ORG";

const ROLLS = [roll({ id: "ra", roll_no: "R-A" }), roll({ id: "rb", roll_no: "R-B" })];

describe("InventoryRollsPage", () => {
  beforeEach(() => {
    listRolls.mockReset();
    listStock.mockReset();
    listRolls.mockResolvedValue({ items: ROLLS, next_cursor: null });
  });

  it("explains a missing grant", async () => {
    renderScreen(<InventoryRollsPage />, []);
    expect(await screen.findByText("You don't have access to rolls")).toBeDefined();
    expect(listRolls).not.toHaveBeenCalled();
  });

  it("gives a reader the rolls with no selection boxes and no create button", async () => {
    renderScreen(<InventoryRollsPage />, [READ]);
    expect((await screen.findAllByText("R-A")).length).toBeGreaterThan(0);
    expect(screen.queryByRole("checkbox")).toBeNull();
    expect(screen.queryByRole("button", { name: "Create roll" })).toBeNull();
  });

  it("explains an empty list, and offers the first roll only to a writer", async () => {
    listRolls.mockResolvedValue({ items: [], next_cursor: null });
    renderScreen(<InventoryRollsPage />, [READ, WRITE]);
    expect(await screen.findByText("No rolls yet")).toBeDefined();
    expect(screen.getAllByRole("button", { name: "Create roll" }).length).toBeGreaterThan(0);
  });

  it("sends the state filter to the server and says when nothing matches", async () => {
    const user = userEvent.setup();
    renderScreen(<InventoryRollsPage />, [READ]);
    await screen.findAllByText("R-A");

    listRolls.mockResolvedValue({ items: [], next_cursor: null });
    await user.selectOptions(screen.getByLabelText("State"), "SPLIT");

    expect(await screen.findByText("No rolls match these filters")).toBeDefined();
    expect(listRolls).toHaveBeenLastCalledWith(expect.objectContaining({ state: "SPLIT" }));
  });

  it("keeps Merge rolls off until two are ticked, then opens the merge for exactly those", async () => {
    const user = userEvent.setup();
    listStock.mockImplementation(({ roll_id }: { roll_id: string }) =>
      Promise.resolve({ items: [stockRow({ roll_id, on_hand_qty: roll_id === "ra" ? "50" : "32.5" })], next_cursor: null }),
    );
    renderScreen(<InventoryRollsPage />, [READ, WRITE]);
    await screen.findAllByText("R-A");

    const boxes = await screen.findAllByRole("checkbox", { name: /Select roll R-/ });
    expect(boxes.length).toBeGreaterThanOrEqual(2);
    await user.click(screen.getAllByRole("checkbox", { name: "Select roll R-A" })[0] as HTMLElement);
    const merge = await screen.findByRole("button", { name: "Merge rolls" });
    expect(merge).toHaveProperty("disabled", true);

    await user.click(screen.getAllByRole("checkbox", { name: "Select roll R-B" })[0] as HTMLElement);
    await waitFor(() => expect(screen.getByRole("button", { name: "Merge rolls" })).toHaveProperty("disabled", false));
    await user.click(screen.getByRole("button", { name: "Merge rolls" }));

    expect(await screen.findByRole("dialog", { name: "Merge 2 rolls" })).toBeDefined();
    await waitFor(() => expect(listStock).toHaveBeenCalledTimes(2));
  });
});
