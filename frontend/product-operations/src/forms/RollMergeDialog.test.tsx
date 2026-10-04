/**
 * Roll merge: only a merge that can work is offered. The buckets every roll shares come from their
 * stock; what the client can see (item, lot, shade, state) is checked first; and the server's
 * `roll_ids/not_mergeable` and `roll_no/duplicate` refusals land on the selection and the number.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { insufficientStock, problem, roll, stockRow } from "@/test-utils/tracking";
import { RollMergeDialog } from "./RollMergeDialog";

const listStock = vi.fn();
const mergeRolls = vi.fn();

vi.mock("@/api/tracking", () => ({
  listStock: (...args: unknown[]) => listStock(...args),
  mergeRolls: (...args: unknown[]) => mergeRolls(...args),
}));

const A = roll({ id: "ra", roll_no: "R-A" });
const B = roll({ id: "rb", roll_no: "R-B" });
const RESULT = { move: {}, result: roll({ id: "rm", roll_no: "R-M" }), sources: [A, B] };

function stockFor(rollId: string, quantity: string, location = "loc-1") {
  return {
    items: [stockRow({ roll_id: rollId, on_hand_qty: quantity, location_id: location, location_code: "A-01" })],
    next_cursor: null,
  };
}

async function renderDialog(rolls = [A, B]) {
  const user = userEvent.setup();
  const onSaved = vi.fn();
  render(<RollMergeDialog rolls={rolls} onClose={vi.fn()} onSaved={onSaved} />);
  await screen.findByRole("button", { name: "Merge rolls" });
  return { user, onSaved };
}

describe("RollMergeDialog", () => {
  beforeEach(() => {
    listStock.mockReset();
    mergeRolls.mockReset();
    listStock.mockImplementation(({ roll_id }: { roll_id: string }) =>
      Promise.resolve(roll_id === "ra" ? stockFor("ra", "50") : stockFor("rb", "32.5")),
    );
  });

  it("says why rolls of different shades can't merge, with no submit", async () => {
    render(<RollMergeDialog rolls={[A, roll({ id: "rb", shade: "Red" })]} onClose={vi.fn()} onSaved={vi.fn()} />);
    expect(await screen.findByText(/different shades/)).toBeDefined();
    expect(screen.queryByRole("button", { name: "Merge rolls" })).toBeNull();
    expect(listStock).not.toHaveBeenCalled();
  });

  it("explains rolls that share no location and status", async () => {
    listStock.mockImplementation(({ roll_id }: { roll_id: string }) =>
      Promise.resolve(roll_id === "ra" ? stockFor("ra", "50", "loc-1") : stockFor("rb", "32.5", "loc-2")),
    );
    render(<RollMergeDialog rolls={[A, B]} onClose={vi.fn()} onSaved={vi.fn()} />);
    expect(await screen.findByText("These rolls share no location")).toBeDefined();
    expect(screen.queryByRole("button", { name: "Merge rolls" })).toBeNull();
  });

  it("shows the loading state while the rolls' stock is read", () => {
    listStock.mockReturnValue(new Promise(() => undefined));
    render(<RollMergeDialog rolls={[A, B]} onClose={vi.fn()} onSaved={vi.fn()} />);
    expect(screen.getByText("Loading the stock these rolls hold…")).toBeDefined();
  });

  it("shows the combined quantity the new roll will hold, then sends ids, bucket and a key", async () => {
    mergeRolls.mockResolvedValue(RESULT);
    const { user, onSaved } = await renderDialog();
    expect(screen.getByText("The new roll will hold 82.5 m.")).toBeDefined();

    await user.type(screen.getByLabelText(/New roll number/), "R-M");
    await user.click(screen.getByRole("button", { name: "Merge rolls" }));

    await waitFor(() => expect(mergeRolls).toHaveBeenCalledTimes(1));
    const [body, key] = mergeRolls.mock.calls[0] as [Record<string, unknown>, string];
    expect(body).toMatchObject({ roll_ids: ["ra", "rb"], location_id: "loc-1", status: "AVAILABLE", roll_no: "R-M" });
    expect(key).toMatch(/^[0-9a-f-]{36}$/);
    expect(onSaved).toHaveBeenCalledWith(RESULT);
  });

  it("shows the server's not_mergeable sentence on the selection", async () => {
    const sentence = "Rolls differ in shade: Indigo and Red";
    mergeRolls.mockRejectedValue(problem(422, sentence, [{ field: "roll_ids", code: "not_mergeable", message: sentence }]));
    const { user } = await renderDialog();
    await user.click(screen.getByRole("button", { name: "Merge rolls" }));

    expect(await screen.findByText(sentence)).toBeDefined();
  });

  it("puts a duplicate roll number on the number field and keeps it", async () => {
    const sentence = "A roll numbered 'R-M' already exists";
    mergeRolls.mockRejectedValue(problem(409, sentence, [{ field: "roll_no", code: "duplicate", message: sentence }], "conflict"));
    const { user } = await renderDialog();
    await user.type(screen.getByLabelText(/New roll number/), "R-M");
    await user.click(screen.getByRole("button", { name: "Merge rolls" }));

    expect(await screen.findByText(sentence)).toBeDefined();
    expect((screen.getByLabelText(/New roll number/) as HTMLInputElement).value).toBe("R-M");
  });

  it("shows reserved stock standing in the way", async () => {
    mergeRolls.mockRejectedValue(
      insufficientStock([{ reservation_id: "res-1", reference_type: "order", reference_id: "aabbccddee", quantity: "60" }]),
    );
    const { user } = await renderDialog();
    await user.click(screen.getByRole("button", { name: "Merge rolls" }));

    expect(await screen.findByText("Reserved stock is in the way")).toBeDefined();
  });
});
