/**
 * Create roll: the length is a decimal string checked against the item's unit; weight and its unit
 * go together; a lot-tracked item needs its lot (and the server's `lot_id/required` lands on it);
 * a duplicate roll number lands on the number.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { item, lot, problem, roll } from "@/test-utils/tracking";
import { RollFormDialog } from "./RollFormDialog";

const createRoll = vi.fn();
const listLots = vi.fn();

vi.mock("@/api/tracking", () => ({
  createRoll: (...args: unknown[]) => createRoll(...args),
  listLots: (...args: unknown[]) => listLots(...args),
}));
vi.mock("@/api/client", () => ({
  searchInventory: () => Promise.resolve([]),
}));

const ROLL_ITEM = item({ track_roll: true });

function renderDialog(forItem = ROLL_ITEM) {
  const user = userEvent.setup();
  const onSaved = vi.fn();
  render(<RollFormDialog item={forItem} onClose={vi.fn()} onSaved={onSaved} />);
  return { user, onSaved };
}

describe("RollFormDialog", () => {
  beforeEach(() => {
    createRoll.mockReset();
    listLots.mockReset();
    listLots.mockResolvedValue({ items: [lot({ id: "lot-1", lot_no: "LOT-1" })], next_cursor: null });
  });

  it("needs a length", async () => {
    const { user } = renderDialog();
    await user.click(screen.getByRole("button", { name: "Create roll" }));
    expect(await screen.findByText("Enter a quantity.")).toBeDefined();
    expect(createRoll).not.toHaveBeenCalled();
  });

  it("sends the length and weight as decimal strings, the measurements as integers, and a key", async () => {
    createRoll.mockResolvedValue(roll());
    const { user, onSaved } = renderDialog();
    await user.type(screen.getByLabelText(/Length/), "82.5");
    await user.type(screen.getByLabelText("Measured weight (optional)"), "14.8");
    await user.type(screen.getByLabelText(/Weight unit/), "kg");
    await user.type(screen.getByLabelText(/Width \(mm\)/), "1500");
    await user.type(screen.getByLabelText(/GSM/), "120");
    await user.type(screen.getByLabelText(/Shade/), "Indigo");
    await user.click(screen.getByRole("button", { name: "Create roll" }));

    await waitFor(() => expect(createRoll).toHaveBeenCalledTimes(1));
    const [body, key] = createRoll.mock.calls[0] as [Record<string, unknown>, string];
    expect(body).toEqual({
      inventory_item_id: "item-1",
      primary_qty: "82.5",
      secondary_qty: "14.8",
      secondary_uom: "kg",
      width_mm: 1500,
      gsm: 120,
      shade: "Indigo",
    });
    expect(typeof body["primary_qty"]).toBe("string");
    expect(typeof body["secondary_qty"]).toBe("string");
    expect(key).toMatch(/^[0-9a-f-]{36}$/);
    expect(onSaved).toHaveBeenCalledWith(roll());
  });

  it("refuses a weight without its unit, and a unit without its weight", async () => {
    const { user } = renderDialog();
    await user.type(screen.getByLabelText(/Length/), "10");
    await user.type(screen.getByLabelText("Measured weight (optional)"), "2");
    await user.click(screen.getByRole("button", { name: "Create roll" }));
    expect(await screen.findByText("Say what the weight is measured in, like kg.")).toBeDefined();

    await user.clear(screen.getByLabelText("Measured weight (optional)"));
    await user.type(screen.getByLabelText(/Weight unit/), "kg");
    await user.click(screen.getByRole("button", { name: "Create roll" }));
    expect(await screen.findByText("Enter the measured quantity, or clear the unit.")).toBeDefined();
    expect(createRoll).not.toHaveBeenCalled();
  });

  it("refuses non-integer measurements", async () => {
    const { user } = renderDialog();
    await user.type(screen.getByLabelText(/Length/), "10");
    await user.type(screen.getByLabelText(/GSM/), "12.5");
    await user.click(screen.getByRole("button", { name: "Create roll" }));
    expect(await screen.findByText("GSM is a whole number, like 150.")).toBeDefined();
  });

  it("refuses a fractional length of a discrete unit", async () => {
    const { user } = renderDialog(item({ track_roll: true, uom: "roll" }));
    await user.type(screen.getByLabelText(/Length/), "1.5");
    await user.click(screen.getByRole("button", { name: "Create roll" }));
    expect(await screen.findByText("roll are counted in whole units — remove the decimals.")).toBeDefined();
  });

  it("requires a lot for a lot-tracked item and sends the chosen one", async () => {
    createRoll.mockResolvedValue(roll());
    const { user } = renderDialog(item({ track_roll: true, track_lot: true }));
    await screen.findByRole("option", { name: "LOT-1" });
    await user.type(screen.getByLabelText(/Length/), "10");
    await user.click(screen.getByRole("button", { name: "Create roll" }));
    expect(await screen.findByText("Choose the lot this roll belongs to.")).toBeDefined();
    expect(createRoll).not.toHaveBeenCalled();

    await user.selectOptions(screen.getByLabelText("Lot"), "lot-1");
    await user.click(screen.getByRole("button", { name: "Create roll" }));
    await waitFor(() => expect(createRoll).toHaveBeenCalledTimes(1));
    expect(createRoll.mock.calls[0]?.[0]).toMatchObject({ lot_id: "lot-1" });
  });

  it("maps the server's lot_id/required onto the lot", async () => {
    createRoll.mockRejectedValue(
      problem(422, "FAB-100 is tracked by lot; a roll of it belongs to a lot", [
        { field: "lot_id", code: "required", message: "lot required" },
      ]),
    );
    const { user } = renderDialog(item({ track_roll: true, track_lot: true }));
    await screen.findByRole("option", { name: "LOT-1" });
    await user.selectOptions(screen.getByLabelText("Lot"), "lot-1");
    await user.type(screen.getByLabelText(/Length/), "10");
    await user.click(screen.getByRole("button", { name: "Create roll" }));

    expect(await screen.findByText("Choose the lot this stock belongs to.")).toBeDefined();
    expect((screen.getByLabelText(/Length/) as HTMLInputElement).value).toBe("10");
  });

  it("puts a duplicate roll number on the number field and keeps every entry", async () => {
    const sentence = "A roll numbered 'R-1' already exists";
    createRoll.mockRejectedValue(problem(409, sentence, [{ field: "roll_no", code: "duplicate", message: sentence }], "conflict"));
    const { user } = renderDialog();
    await user.type(screen.getByLabelText(/Roll number/), "R-1");
    await user.type(screen.getByLabelText(/Length/), "10");
    await user.click(screen.getByRole("button", { name: "Create roll" }));

    expect(await screen.findByText(sentence)).toBeDefined();
    expect((screen.getByLabelText(/Roll number/) as HTMLInputElement).value).toBe("R-1");
    expect((screen.getByLabelText(/Length/) as HTMLInputElement).value).toBe("10");
  });
});
