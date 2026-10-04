/**
 * Roll split: the children must add up exactly to what the roll's bucket holds. The dialog loads
 * that figure, shows the running total, refuses an inexact sum before sending, and maps the
 * server's `children/split_total` and `children/duplicate` refusals onto the children.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiError } from "@opsgrids/client";
import { insufficientStock, move, problem, roll, stockRow } from "@/test-utils/tracking";
import { RollSplitDialog } from "./RollSplitDialog";

const listStock = vi.fn();
const splitRoll = vi.fn();

vi.mock("@/api/tracking", () => ({
  listStock: (...args: unknown[]) => listStock(...args),
  splitRoll: (...args: unknown[]) => splitRoll(...args),
}));

const ROLL = roll();
const RESULT = { move: move(), parent: { ...ROLL, state: "SPLIT" }, children: [roll({ id: "c1" }), roll({ id: "c2" })] };

async function renderDialog() {
  const user = userEvent.setup();
  const onSaved = vi.fn();
  render(<RollSplitDialog roll={ROLL} onClose={vi.fn()} onSaved={onSaved} />);
  await screen.findByRole("button", { name: "Split roll" });
  return { user, onSaved };
}

describe("RollSplitDialog — states", () => {
  beforeEach(() => {
    listStock.mockReset();
    splitRoll.mockReset();
  });

  it("says it is loading the roll's stock, with no submit button yet", () => {
    listStock.mockReturnValue(new Promise(() => undefined));
    render(<RollSplitDialog roll={ROLL} onClose={vi.fn()} onSaved={vi.fn()} />);
    expect(screen.getByText("Loading the stock this roll holds…")).toBeDefined();
    expect(screen.queryByRole("button", { name: "Split roll" })).toBeNull();
  });

  it("offers a retry when the roll's stock can't be loaded", async () => {
    const user = userEvent.setup();
    listStock.mockRejectedValueOnce(new ApiError(500, "Stock is unavailable", { detail: "Stock is unavailable" }));
    listStock.mockResolvedValueOnce({ items: [stockRow({ roll_id: "roll-1" })], next_cursor: null });
    render(<RollSplitDialog roll={ROLL} onClose={vi.fn()} onSaved={vi.fn()} />);

    expect(await screen.findByText("Stock is unavailable")).toBeDefined();
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("button", { name: "Split roll" })).toBeDefined();
  });

  it("explains an empty roll instead of showing a form that cannot work", async () => {
    listStock.mockResolvedValue({ items: [stockRow({ roll_id: "roll-1", on_hand_qty: "0" })], next_cursor: null });
    render(<RollSplitDialog roll={ROLL} onClose={vi.fn()} onSaved={vi.fn()} />);

    expect(await screen.findByText("Nothing to split")).toBeDefined();
    expect(screen.getByText(/holds no stock/)).toBeDefined();
    expect(screen.queryByRole("button", { name: "Split roll" })).toBeNull();
  });

  it("explains a roll that was already split", async () => {
    listStock.mockResolvedValue({ items: [stockRow()], next_cursor: null });
    render(<RollSplitDialog roll={roll({ state: "SPLIT" })} onClose={vi.fn()} onSaved={vi.fn()} />);
    expect(await screen.findByText("This roll has already been split or merged.")).toBeDefined();
  });
});

describe("RollSplitDialog — the exact-sum rule", () => {
  beforeEach(() => {
    listStock.mockReset();
    splitRoll.mockReset();
    listStock.mockResolvedValue({ items: [stockRow({ roll_id: "roll-1", on_hand_qty: "82.5" })], next_cursor: null });
  });

  it("shows what is left to allocate as the children are typed, and when it matches exactly", async () => {
    const { user } = await renderDialog();
    expect(screen.getByText(/Children total 0 of 82.5 m\. 82.5 m left to allocate\./)).toBeDefined();

    await user.type(screen.getByLabelText("Child 1 quantity (m)"), "50");
    expect(screen.getByText(/Children total 50 of 82.5 m\. 32.5 m left to allocate\./)).toBeDefined();

    await user.type(screen.getByLabelText("Child 2 quantity (m)"), "32.5");
    expect(screen.getByText(/Children total 82.5 of 82.5 m\. Matches exactly\./)).toBeDefined();
  });

  it("reports an overshoot", async () => {
    const { user } = await renderDialog();
    await user.type(screen.getByLabelText("Child 1 quantity (m)"), "60");
    await user.type(screen.getByLabelText("Child 2 quantity (m)"), "30");
    expect(screen.getByText(/Over by 7.5 m\./)).toBeDefined();
  });

  it("refuses an inexact sum before sending, naming both figures", async () => {
    const { user } = await renderDialog();
    await user.type(screen.getByLabelText("Child 1 quantity (m)"), "50");
    await user.type(screen.getByLabelText("Child 2 quantity (m)"), "30");
    await user.click(screen.getByRole("button", { name: "Split roll" }));

    expect(
      await screen.findByText("The children add up to 80 m, but this roll holds 82.5 m. They must match exactly."),
    ).toBeDefined();
    expect(splitRoll).not.toHaveBeenCalled();
  });

  it("does not drift on decimals: 0.1 + 0.2 splits a 0.3 bucket", async () => {
    listStock.mockResolvedValue({ items: [stockRow({ roll_id: "roll-1", on_hand_qty: "0.3" })], next_cursor: null });
    splitRoll.mockResolvedValue(RESULT);
    const { user } = await renderDialog();
    await user.type(screen.getByLabelText("Child 1 quantity (m)"), "0.1");
    await user.type(screen.getByLabelText("Child 2 quantity (m)"), "0.2");
    await user.click(screen.getByRole("button", { name: "Split roll" }));

    await waitFor(() => expect(splitRoll).toHaveBeenCalledTimes(1));
  });

  it("checks each child's quantity on its own row", async () => {
    const { user } = await renderDialog();
    await user.type(screen.getByLabelText("Child 1 quantity (m)"), "50");
    await user.type(screen.getByLabelText("Child 2 quantity (m)"), "1.2345");
    await user.click(screen.getByRole("button", { name: "Split roll" }));

    expect(await screen.findByText("Use at most 3 decimal places.")).toBeDefined();
    expect(splitRoll).not.toHaveBeenCalled();
  });

  it("adds and removes children, keeping at least two", async () => {
    const { user } = await renderDialog();
    expect(screen.queryByRole("button", { name: /Remove child/ })).toBeNull();

    await user.click(screen.getByRole("button", { name: "Add child" }));
    expect(screen.getByLabelText("Child 3 quantity (m)")).toBeDefined();
    await user.click(screen.getByRole("button", { name: "Remove child 3" }));
    expect(screen.queryByLabelText("Child 3 quantity (m)")).toBeNull();
  });

  it("sends decimal-string children with optional numbers, the bucket and a key, for the roll's own id", async () => {
    splitRoll.mockResolvedValue(RESULT);
    const { user, onSaved } = await renderDialog();
    await user.type(screen.getByLabelText("Child 1 quantity (m)"), "50");
    await user.type(screen.getByLabelText(/Child 1 roll number/), "R-001-A");
    await user.type(screen.getByLabelText("Child 2 quantity (m)"), "32.5");
    await user.click(screen.getByRole("button", { name: "Split roll" }));

    await waitFor(() => expect(splitRoll).toHaveBeenCalledTimes(1));
    const [id, body, key] = splitRoll.mock.calls[0] as [string, Record<string, unknown>, string];
    expect(id).toBe("roll-1");
    expect(body).toMatchObject({
      location_id: "loc-1",
      status: "AVAILABLE",
      children: [{ quantity: "50", roll_no: "R-001-A" }, { quantity: "32.5" }],
    });
    expect(key).toMatch(/^[0-9a-f-]{36}$/);
    expect(onSaved).toHaveBeenCalledWith(RESULT);
  });

  it("shows the server's split_total sentence on the children and keeps every entry", async () => {
    const { user } = await renderDialog();
    const sentence = "children add up to 82.499 m but roll holds 82.5 m";
    splitRoll.mockRejectedValue(
      problem(422, sentence, [{ field: "children", code: "split_total", message: sentence }]),
    );
    await user.type(screen.getByLabelText("Child 1 quantity (m)"), "50");
    await user.type(screen.getByLabelText("Child 2 quantity (m)"), "32.5");
    await user.click(screen.getByRole("button", { name: "Split roll" }));

    expect(await screen.findByText(sentence)).toBeDefined();
    expect((screen.getByLabelText("Child 1 quantity (m)") as HTMLInputElement).value).toBe("50");
    expect((screen.getByLabelText("Child 2 quantity (m)") as HTMLInputElement).value).toBe("32.5");
  });

  it("shows a duplicate child number from the server on the children", async () => {
    const { user } = await renderDialog();
    const sentence = "Two children share a roll number";
    splitRoll.mockRejectedValue(problem(409, sentence, [{ field: "children", code: "duplicate", message: sentence }], "conflict"));
    await user.type(screen.getByLabelText("Child 1 quantity (m)"), "50");
    await user.type(screen.getByLabelText("Child 2 quantity (m)"), "32.5");
    await user.click(screen.getByRole("button", { name: "Split roll" }));

    expect(await screen.findByText(sentence)).toBeDefined();
  });

  it("shows reserved stock standing in the way, with the reservations", async () => {
    const { user } = await renderDialog();
    splitRoll.mockRejectedValue(
      insufficientStock([{ reservation_id: "res-1", reference_type: "order", reference_id: "99887766ff", quantity: "60" }]),
    );
    await user.type(screen.getByLabelText("Child 1 quantity (m)"), "50");
    await user.type(screen.getByLabelText("Child 2 quantity (m)"), "32.5");
    await user.click(screen.getByRole("button", { name: "Split roll" }));

    expect(await screen.findByText("Reserved stock is in the way")).toBeDefined();
    expect(screen.getByText(/Order 99887766 — 60 m/)).toBeDefined();
  });
});
