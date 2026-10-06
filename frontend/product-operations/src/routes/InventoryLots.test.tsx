/**
 * The lots list: its four states, the filters reaching the server, and the create control
 * following `operations.inventory.lot.write`.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiError } from "@opsgrids/client";
import { renderScreen } from "@/test-utils/renderScreen";
import { lot } from "@/test-utils/tracking";
import { InventoryLotsPage } from "./InventoryLots";

const listLots = vi.fn();

vi.mock("@/api/tracking", () => ({
  listLots: (...args: unknown[]) => listLots(...args),
  createLot: () => Promise.reject(new Error("not used")),
}));
vi.mock("@/api/client", () => ({
  searchInventory: () => Promise.resolve([]),
}));

const READ = "operations.inventory.stock.read:ORG";
const WRITE = "operations.inventory.lot.write:ORG";

describe("InventoryLotsPage", () => {
  beforeEach(() => {
    listLots.mockReset();
    listLots.mockResolvedValue({ items: [lot({ lot_no: "LOT-7", quality_status: "ON_HOLD", on_hand_qty: "12.25" })], next_cursor: null });
  });

  it("explains a missing grant and reads nothing", async () => {
    renderScreen(<InventoryLotsPage />, []);
    expect(await screen.findByText("You don't have access to lots")).toBeDefined();
    expect(listLots).not.toHaveBeenCalled();
  });

  it("lists lots with quality as a word and the exact on-hand figure", async () => {
    renderScreen(<InventoryLotsPage />, [READ]);
    expect((await screen.findAllByText("LOT-7")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("On hold").length).toBeGreaterThan(0);
    expect(screen.getAllByText("12.25").length).toBeGreaterThan(0);
  });

  it("shows loading, then an error with a retry", async () => {
    const user = userEvent.setup();
    listLots.mockRejectedValueOnce(new ApiError(500, "Lots unavailable", { detail: "Lots unavailable" }));
    renderScreen(<InventoryLotsPage />, [READ]);

    expect(await screen.findByText("Lots unavailable")).toBeDefined();
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect((await screen.findAllByText("LOT-7")).length).toBeGreaterThan(0);
  });

  it("explains an empty list, offering the first lot only to a writer", async () => {
    listLots.mockResolvedValue({ items: [], next_cursor: null });
    const { unmount } = renderScreen(<InventoryLotsPage />, [READ, WRITE]);
    expect(await screen.findByText("No lots yet")).toBeDefined();
    expect(screen.getAllByRole("button", { name: "Create lot" }).length).toBeGreaterThan(0);
    unmount();

    renderScreen(<InventoryLotsPage />, [READ]);
    await screen.findByText("No lots yet");
    expect(screen.queryByRole("button", { name: "Create lot" })).toBeNull();
  });

  it("sends the quality status and the lot-number search to the server", async () => {
    const user = userEvent.setup();
    renderScreen(<InventoryLotsPage />, [READ]);
    await screen.findAllByText("LOT-7");

    await user.selectOptions(screen.getByLabelText("Quality status"), "REJECTED");
    expect(listLots).toHaveBeenLastCalledWith(expect.objectContaining({ quality_status: "REJECTED" }));

    listLots.mockResolvedValue({ items: [], next_cursor: null });
    await user.type(screen.getByLabelText("Search lots by number"), "LOT-9");
    expect(await screen.findByText("No lots match these filters")).toBeDefined();
    expect(listLots).toHaveBeenLastCalledWith(expect.objectContaining({ q: "LOT-9" }));
  });
});
