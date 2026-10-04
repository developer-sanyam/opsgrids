/**
 * Opening stock: a tracked item must name its lot and roll, an untracked one must not; the
 * quantity is a decimal string checked against the item's unit; the server's `lot_id/required`,
 * `roll_id/required` and `quantity` refusals land on those controls.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { item, lot, move, problem, roll } from "@/test-utils/tracking";
import { OpeningStockDialog } from "./OpeningStockDialog";

// The dialog reads the warehouses, then the item's own warehouse's locations: two awaited fetches in
// a row, which a starved CI worker can stretch well past testing-library's default second.
const LOAD_TIMEOUT = 15_000;
vi.setConfig({ testTimeout: 30_000 });

const postOpeningStock = vi.fn();
const listLots = vi.fn();
const listRolls = vi.fn();
const listWarehouses = vi.fn();
const listLocations = vi.fn();

vi.mock("@/api/tracking", () => ({
  postOpeningStock: (...args: unknown[]) => postOpeningStock(...args),
  listLots: (...args: unknown[]) => listLots(...args),
  listRolls: (...args: unknown[]) => listRolls(...args),
}));
vi.mock("@/api/client", () => ({
  FULL_PAGE: 200,
  listWarehouses: (...args: unknown[]) => listWarehouses(...args),
  listLocations: (...args: unknown[]) => listLocations(...args),
}));

async function renderDialog(forItem = item()) {
  const user = userEvent.setup();
  const onSaved = vi.fn();
  render(<OpeningStockDialog item={forItem} onClose={vi.fn()} onSaved={onSaved} />);
  // The item's own warehouse is chosen once the directory loads, then its locations.
  await screen.findByRole("option", { name: "A-01 — Bin A-01" }, { timeout: LOAD_TIMEOUT });
  return { user, onSaved };
}

describe("OpeningStockDialog", () => {
  beforeEach(() => {
    postOpeningStock.mockReset();
    listLots.mockReset();
    listRolls.mockReset();
    listWarehouses.mockReset();
    listLocations.mockReset();
    listWarehouses.mockResolvedValue({ items: [{ id: "wh-1", name: "Bhiwandi DC" }], total: 1, page: 1, size: 200, pages: 1 });
    listLocations.mockResolvedValue([{ id: "loc-1", code: "A-01", name: "Bin A-01" }]);
    listLots.mockResolvedValue({ items: [lot({ id: "lot-1", lot_no: "LOT-1" })], next_cursor: null });
    listRolls.mockResolvedValue({ items: [roll({ id: "roll-1", roll_no: "R-001" })], next_cursor: null });
  });

  it("starts in the item's own warehouse", async () => {
    await renderDialog();
    expect((screen.getByLabelText("Warehouse") as HTMLSelectElement).value).toBe("wh-1");
  });

  it("holds the request until a location and a valid quantity are given", async () => {
    const { user } = await renderDialog();
    await user.click(screen.getByRole("button", { name: "Post opening stock" }));

    expect(await screen.findByText("Choose the location the stock is in.")).toBeDefined();
    expect(screen.getByText("Enter a quantity.")).toBeDefined();
    expect(postOpeningStock).not.toHaveBeenCalled();
  });

  it("asks an untracked item for neither lot nor roll, and sends neither", async () => {
    postOpeningStock.mockResolvedValue(move({ kind: "opening" }));
    const { user, onSaved } = await renderDialog();
    expect(screen.queryByLabelText("Lot")).toBeNull();
    expect(screen.queryByLabelText("Roll")).toBeNull();

    await user.selectOptions(screen.getByLabelText("Location"), "loc-1");
    await user.type(screen.getByLabelText(/Quantity/), "82.5");
    await user.click(screen.getByRole("button", { name: "Post opening stock" }));

    await waitFor(() => expect(postOpeningStock).toHaveBeenCalledTimes(1));
    const [body, key] = postOpeningStock.mock.calls[0] as [{ lines: Record<string, unknown>[] }, string];
    expect(body.lines).toEqual([
      { inventory_item_id: "item-1", location_id: "loc-1", quantity: "82.5", status: "AVAILABLE" },
    ]);
    expect(typeof body.lines[0]?.["quantity"]).toBe("string");
    expect(key).toMatch(/^[0-9a-f-]{36}$/);
    expect(onSaved).toHaveBeenCalled();
  });

  it("requires a lot and a roll of a tracked item before sending", async () => {
    const { user } = await renderDialog(item({ track_lot: true, track_roll: true }));
    await user.selectOptions(screen.getByLabelText("Location"), "loc-1");
    await user.type(screen.getByLabelText(/Quantity/), "50");
    await user.click(screen.getByRole("button", { name: "Post opening stock" }));

    expect(await screen.findByText("Choose the lot this stock belongs to.")).toBeDefined();
    expect(screen.getByText("Choose the roll this stock belongs to.")).toBeDefined();
    expect(postOpeningStock).not.toHaveBeenCalled();
  });

  it("sends the chosen lot and roll for a tracked item", async () => {
    postOpeningStock.mockResolvedValue(move());
    const { user } = await renderDialog(item({ track_lot: true, track_roll: true }));
    await screen.findByRole("option", { name: "LOT-1" }, { timeout: LOAD_TIMEOUT });
    await user.selectOptions(screen.getByLabelText("Location"), "loc-1");
    await user.selectOptions(screen.getByLabelText("Lot"), "lot-1");
    await screen.findByRole("option", { name: "R-001" }, { timeout: LOAD_TIMEOUT });
    await user.selectOptions(screen.getByLabelText("Roll"), "roll-1");
    await user.type(screen.getByLabelText(/Quantity/), "50");
    await user.click(screen.getByRole("button", { name: "Post opening stock" }));

    await waitFor(() => expect(postOpeningStock).toHaveBeenCalledTimes(1));
    const [body] = postOpeningStock.mock.calls[0] as [{ lines: Record<string, unknown>[] }];
    expect(body.lines[0]).toMatchObject({ lot_id: "lot-1", roll_id: "roll-1", quantity: "50" });
  });

  it("refuses decimals of a discrete unit", async () => {
    const { user } = await renderDialog(item({ uom: "pcs" }));
    await user.selectOptions(screen.getByLabelText("Location"), "loc-1");
    await user.type(screen.getByLabelText(/Quantity/), "1.5");
    await user.click(screen.getByRole("button", { name: "Post opening stock" }));

    expect(await screen.findByText("pcs are counted in whole units — remove the decimals.")).toBeDefined();
    expect(postOpeningStock).not.toHaveBeenCalled();
  });

  it("maps the server's lot_id/required and roll_id/required onto the lot and roll", async () => {
    postOpeningStock.mockRejectedValue(
      problem(422, "FAB-100 is tracked by lot; say which lot this stock is", [
        { field: "lot_id", code: "required", message: "lot required" },
        { field: "roll_id", code: "required", message: "roll required" },
      ]),
    );
    const { user } = await renderDialog(item({ track_lot: true, track_roll: true }));
    await screen.findByRole("option", { name: "LOT-1" }, { timeout: LOAD_TIMEOUT });
    await user.selectOptions(screen.getByLabelText("Location"), "loc-1");
    await user.selectOptions(screen.getByLabelText("Lot"), "lot-1");
    await screen.findByRole("option", { name: "R-001" }, { timeout: LOAD_TIMEOUT });
    await user.selectOptions(screen.getByLabelText("Roll"), "roll-1");
    await user.type(screen.getByLabelText(/Quantity/), "50");
    await user.click(screen.getByRole("button", { name: "Post opening stock" }));

    // Both fields were chosen client-side; the server's refusal still lands on them, in words.
    expect(await screen.findAllByText("Choose the lot this stock belongs to.")).toHaveLength(1);
    expect(screen.getByText("Choose the roll this stock belongs to.")).toBeDefined();
    expect((screen.getByLabelText(/Quantity/) as HTMLInputElement).value).toBe("50");
  });

  it("maps a line-scoped quantity refusal onto the quantity field", async () => {
    postOpeningStock.mockRejectedValue(
      problem(422, "x", [{ field: "lines.0.quantity", code: "invalid_quantity", message: "must be > 0" }]),
    );
    const { user } = await renderDialog();
    await user.selectOptions(screen.getByLabelText("Location"), "loc-1");
    await user.type(screen.getByLabelText(/Quantity/), "5");
    await user.click(screen.getByRole("button", { name: "Post opening stock" }));

    expect(await screen.findByText("must be > 0")).toBeDefined();
  });

  it("maps not_tracked from the server onto the lot", async () => {
    postOpeningStock.mockRejectedValue(
      problem(422, "FAB-100 is not tracked by lot", [{ field: "lot_id", code: "not_tracked", message: "not tracked" }]),
    );
    const { user } = await renderDialog();
    await user.selectOptions(screen.getByLabelText("Location"), "loc-1");
    await user.type(screen.getByLabelText(/Quantity/), "5");
    await user.click(screen.getByRole("button", { name: "Post opening stock" }));

    // An untracked item has no lot control; the refusal is not lost, it surfaces as the dialog error.
    expect(await screen.findByText("FAB-100 is not tracked by lot")).toBeDefined();
  });

  it("refuses a unit cost above 10**15 minor units, and sends nothing", async () => {
    const { user } = await renderDialog();
    await user.selectOptions(screen.getByLabelText("Location"), "loc-1");
    await user.type(screen.getByLabelText(/Quantity/), "5");
    await user.type(screen.getByLabelText(/Unit cost/), "1000000000000001");
    await user.type(screen.getByLabelText(/Cost currency/), "INR");
    await user.click(screen.getByRole("button", { name: "Post opening stock" }));

    expect(await screen.findByText(/at most 1,000,000,000,000,000 minor units/)).toBeDefined();
    expect(postOpeningStock).not.toHaveBeenCalled();
  });

  it("accepts a unit cost of exactly 10**15 and sends it as an integer with its currency", async () => {
    postOpeningStock.mockResolvedValue(move({ kind: "opening" }));
    const { user } = await renderDialog();
    await user.selectOptions(screen.getByLabelText("Location"), "loc-1");
    await user.type(screen.getByLabelText(/Quantity/), "5");
    await user.type(screen.getByLabelText(/Unit cost/), "1000000000000000");
    await user.type(screen.getByLabelText(/Cost currency/), "inr");
    await user.click(screen.getByRole("button", { name: "Post opening stock" }));

    await waitFor(() => expect(postOpeningStock).toHaveBeenCalledTimes(1));
    const [body] = postOpeningStock.mock.calls[0] as [{ lines: Record<string, unknown>[] }];
    expect(body.lines[0]).toMatchObject({ unit_cost_minor: 10 ** 15, unit_cost_currency: "INR" });
  });

  it("asks for the currency when a unit cost is given without one", async () => {
    const { user } = await renderDialog();
    await user.selectOptions(screen.getByLabelText("Location"), "loc-1");
    await user.type(screen.getByLabelText(/Quantity/), "5");
    await user.type(screen.getByLabelText(/Unit cost/), "1250");
    await user.click(screen.getByRole("button", { name: "Post opening stock" }));

    expect(await screen.findByText("Enter the 3-letter currency code, like INR.")).toBeDefined();
    expect(postOpeningStock).not.toHaveBeenCalled();
  });
});
