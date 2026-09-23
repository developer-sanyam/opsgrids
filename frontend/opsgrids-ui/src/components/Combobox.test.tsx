/**
 * The picker is the one control here a user cannot complete with a mouse alone and still be
 * served — an approver setting up a delegation from a keyboard is the ordinary case, not the
 * accessible edge case. Its arrow keys, its two-character floor and its result announcement are
 * pinned for that reason.
 */

import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { Combobox } from "./Combobox";
import type { ComboboxOption } from "./Combobox";

const CANDIDATES: ComboboxOption[] = [
  { id: "1", label: "Rohan Mehta", sublabel: "EMP-0142" },
  { id: "2", label: "Rohini Das", sublabel: "EMP-0913" },
];

// The real debounce (`DEBOUNCE_MS` in Combobox.tsx) is 200ms — right for a person typing, but not
// worth spending in a test six times over. `userEvent` dispatches a typed string's keystrokes back
// to back with no artificial delay between them, so a debounce window two orders of magnitude
// shorter than the real one still coalesces a burst of keystrokes into the one request the tests
// below assert on; it just does it in single-digit milliseconds instead of 200 of them. (Vitest's
// fake timers were tried first — this file's `userEvent.type` calls deadlock under them in this
// environment, because `@testing-library/react`'s `act`-based `asyncWrapper` depends on real time
// passing to flush React's scheduler, and nothing here can tell that dependency from the debounce
// apart to advance one without the other.)
const TEST_DEBOUNCE_MS = 5;

function Harness({
  loadOptions,
}: {
  loadOptions: (query: string, signal: AbortSignal) => Promise<ComboboxOption[]>;
}) {
  const [value, setValue] = useState<ComboboxOption | null>(null);
  return (
    <Combobox
      label="Delegate"
      value={value}
      onChange={setValue}
      loadOptions={loadOptions}
      debounceMs={TEST_DEBOUNCE_MS}
      helper="They'll be able to decide anything waiting on you while the delegation runs."
    />
  );
}

const resolve = () => Promise.resolve(CANDIDATES);

describe("Combobox", () => {
  it("is a combobox with a listbox, named by its label", async () => {
    const user = userEvent.setup();
    render(<Harness loadOptions={resolve} />);
    const input = screen.getByRole("combobox", { name: "Delegate" });
    await user.click(input);
    expect(input.getAttribute("aria-expanded")).toBe("true");
  });

  it("asks for nothing below the server's minimum query length", async () => {
    const loadOptions = vi.fn(resolve);
    const user = userEvent.setup();
    render(<Harness loadOptions={loadOptions} />);
    await user.type(screen.getByRole("combobox", { name: "Delegate" }), "R");
    expect(screen.getByText("Keep typing — at least two letters.")).toBeDefined();
    expect(loadOptions).not.toHaveBeenCalled();
  });

  it("searches once the query is long enough and announces how many matched", async () => {
    const user = userEvent.setup();
    render(<Harness loadOptions={resolve} />);
    await user.type(screen.getByRole("combobox", { name: "Delegate" }), "Roh");
    await waitFor(() => expect(screen.getAllByRole("option")).toHaveLength(2));
    expect(screen.getByText("2 results")).toBeDefined();
  });

  it("moves the active option with the arrow keys without moving focus", async () => {
    const user = userEvent.setup();
    render(<Harness loadOptions={resolve} />);
    const input = screen.getByRole("combobox", { name: "Delegate" });
    await user.type(input, "Roh");
    await waitFor(() => expect(screen.getAllByRole("option")).toHaveLength(2));

    const first = screen.getAllByRole("option")[0];
    const second = screen.getAllByRole("option")[1];
    expect(input.getAttribute("aria-activedescendant")).toBe(first?.id);

    await user.keyboard("{ArrowDown}");
    expect(input.getAttribute("aria-activedescendant")).toBe(second?.id);
    expect(document.activeElement).toBe(input);

    await user.keyboard("{ArrowDown}");
    expect(input.getAttribute("aria-activedescendant")).toBe(first?.id);

    await user.keyboard("{End}");
    expect(input.getAttribute("aria-activedescendant")).toBe(second?.id);

    await user.keyboard("{Home}");
    expect(input.getAttribute("aria-activedescendant")).toBe(first?.id);
  });

  it("selects with Enter and offers a keyboard-reachable way to undo it", async () => {
    const user = userEvent.setup();
    render(<Harness loadOptions={resolve} />);
    await user.type(screen.getByRole("combobox", { name: "Delegate" }), "Roh");
    await waitFor(() => expect(screen.getAllByRole("option")).toHaveLength(2));
    await user.keyboard("{ArrowDown}{Enter}");

    const remove = screen.getByRole("button", { name: "Remove Rohini Das" });
    expect(remove).toBeDefined();
    await user.click(remove);
    expect(screen.queryByRole("button", { name: "Remove Rohini Das" })).toBeNull();
  });

  it("closes on Escape and keeps the typed text exactly as typed", async () => {
    const user = userEvent.setup();
    render(<Harness loadOptions={resolve} />);
    const input = screen.getByRole("combobox", { name: "Delegate" });
    await user.type(input, "Roh");
    await waitFor(() => expect(screen.getAllByRole("option")).toHaveLength(2));
    await user.keyboard("{Escape}");
    expect(input.getAttribute("aria-expanded")).toBe("false");
    expect((input as HTMLInputElement).value).toBe("Roh");
  });

  it("says nobody matched, rather than showing an empty list", async () => {
    const user = userEvent.setup();
    render(<Harness loadOptions={() => Promise.resolve([])} />);
    await user.type(screen.getByRole("combobox", { name: "Delegate" }), "Zzz");
    await waitFor(() => expect(screen.getByText("No one matches “Zzz”.")).toBeDefined());
  });

  it("offers a retry when the lookup fails, and keeps the typed text", async () => {
    const user = userEvent.setup();
    // Offline until the test says otherwise, not for exactly one call: on a loaded runner the
    // debounce can fire mid-typing, and a one-shot rejection spent on "R" let the "Roh" lookup
    // succeed before the failure was ever on screen.
    let offline = true;
    const loadOptions = vi.fn<(query: string, signal: AbortSignal) => Promise<ComboboxOption[]>>(
      () => (offline ? Promise.reject(new Error("offline")) : Promise.resolve(CANDIDATES)),
    );
    render(<Harness loadOptions={loadOptions} />);
    const input = screen.getByRole("combobox", { name: "Delegate" });
    await user.type(input, "Roh");
    await waitFor(() => expect(screen.getByText("Couldn't load results.")).toBeDefined());
    expect((input as HTMLInputElement).value).toBe("Roh");

    offline = false;
    await user.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(screen.getAllByRole("option")).toHaveLength(2));
  });

  it("says when the server's cap was reached rather than truncating silently", async () => {
    const twenty = Array.from({ length: 20 }, (_, i) => ({
      id: String(i),
      label: `Person ${i}`,
    }));
    const user = userEvent.setup();
    render(<Harness loadOptions={() => Promise.resolve(twenty)} />);
    await user.type(screen.getByRole("combobox", { name: "Delegate" }), "Per");
    await waitFor(() =>
      expect(screen.getByText("Showing the first 20. Keep typing to narrow it down.")).toBeDefined(),
    );
  });
});
