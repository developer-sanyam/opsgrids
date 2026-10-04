/**
 * Create lot: only a lot-tracked item can have one; a duplicate lot number and a `not_tracked`
 * refusal from the server land on the controls that caused them; nothing is lost on failure.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { item, lot, problem } from "@/test-utils/tracking";
import { LotFormDialog } from "./LotFormDialog";

const createLot = vi.fn();

vi.mock("@/api/tracking", () => ({
  createLot: (...args: unknown[]) => createLot(...args),
}));
vi.mock("@/api/client", () => ({
  searchInventory: () => Promise.resolve([]),
}));

const TRACKED = item({ track_lot: true });

function renderDialog(forItem = TRACKED) {
  const user = userEvent.setup();
  const onSaved = vi.fn();
  render(<LotFormDialog item={forItem} onClose={vi.fn()} onSaved={onSaved} />);
  return { user, onSaved };
}

describe("LotFormDialog", () => {
  beforeEach(() => {
    createLot.mockReset();
  });

  it("creates with only what was entered, an idempotency key, and the default quality status", async () => {
    createLot.mockResolvedValue(lot());
    const { user, onSaved } = renderDialog();
    await user.type(screen.getByLabelText(/Lot number/), "LOT-2026-01");
    await user.type(screen.getByLabelText(/Supplier lot number/), "SUP-9");
    await user.click(screen.getByRole("button", { name: "Create lot" }));

    await waitFor(() => expect(createLot).toHaveBeenCalledTimes(1));
    const [body, key] = createLot.mock.calls[0] as [Record<string, unknown>, string];
    expect(body).toEqual({
      inventory_item_id: "item-1",
      quality_status: "PENDING",
      lot_no: "LOT-2026-01",
      supplier_lot_no: "SUP-9",
    });
    expect(key).toMatch(/^[0-9a-f-]{36}$/);
    expect(onSaved).toHaveBeenCalledWith(lot());
  });

  it("generates the number when it is left blank", async () => {
    createLot.mockResolvedValue(lot());
    const { user } = renderDialog();
    await user.click(screen.getByRole("button", { name: "Create lot" }));

    await waitFor(() => expect(createLot).toHaveBeenCalledTimes(1));
    expect(createLot.mock.calls[0]?.[0]).not.toHaveProperty("lot_no");
  });

  it("refuses a malformed lot number and an expiry before manufacture, before asking the server", async () => {
    const { user } = renderDialog();
    await user.type(screen.getByLabelText(/Lot number/), "bad lot!");
    await user.type(screen.getByLabelText(/Manufactured on/), "2026-05-10");
    await user.type(screen.getByLabelText(/Expires on/), "2026-05-01");
    await user.click(screen.getByRole("button", { name: "Create lot" }));

    expect(await screen.findByText(/Letters, numbers, dots, slashes/)).toBeDefined();
    expect(screen.getByText("Expiry can't be before the manufacturing date.")).toBeDefined();
    expect(createLot).not.toHaveBeenCalled();
  });

  it("refuses an item that is not lot-tracked", async () => {
    const { user } = renderDialog(item({ track_lot: false }));
    expect(screen.getByText("FAB-100 isn't tracked by lot")).toBeDefined();
    await user.click(screen.getByRole("button", { name: "Create lot" }));

    expect(createLot).not.toHaveBeenCalled();
  });

  it("puts a duplicate lot number on the number field, in the server's words, and keeps the entry", async () => {
    const sentence = "A lot numbered 'LOT-1' already exists";
    createLot.mockRejectedValue(problem(409, sentence, [{ field: "lot_no", code: "duplicate", message: sentence }], "conflict"));
    const { user, onSaved } = renderDialog();
    await user.type(screen.getByLabelText(/Lot number/), "LOT-1");
    await user.click(screen.getByRole("button", { name: "Create lot" }));

    expect(await screen.findByText(sentence)).toBeDefined();
    expect(onSaved).not.toHaveBeenCalled();
    expect((screen.getByLabelText(/Lot number/) as HTMLInputElement).value).toBe("LOT-1");
  });

  it("shows a not_tracked refusal from the server as the dialog's error when no control owns it", async () => {
    createLot.mockRejectedValue(
      problem(422, "FAB-100 is not tracked by lot; enable lot tracking on the item first", [
        { field: "inventory_item_id", code: "not_tracked", message: "no lots" },
      ]),
    );
    const { user } = renderDialog();
    await user.click(screen.getByRole("button", { name: "Create lot" }));

    expect(await screen.findByText("FAB-100 is not tracked by lot; enable lot tracking on the item first")).toBeDefined();
  });
});
