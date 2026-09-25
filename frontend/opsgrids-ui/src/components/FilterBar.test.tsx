/**
 * A filter row's promises: what is filtered is always visible as a chip, every chip can be taken
 * off, the search waits for a pause before it asks the server, and on a phone the same filters
 * live in a sheet.
 */

import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { stubViewport } from "../test-media";
import { FilterBar } from "./FilterBar";
import { SearchInput } from "./SearchInput";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

const PLANS = [
  { value: "enterprise", label: "Enterprise" },
  { value: "pro", label: "Pro" },
];

const STATUSES = [
  { value: "active", label: "Active" },
  { value: "trial", label: "Trial" },
];

function Harness({ onSearch = vi.fn() }: { onSearch?: (value: string) => void }) {
  const [plan, setPlan] = useState("enterprise");
  const [status, setStatus] = useState("");
  const [query, setQuery] = useState("");
  const [mine, setMine] = useState(true);
  return (
    <FilterBar
      label="Filter accounts"
      search={{
        label: "Search accounts",
        value: query,
        onChange: (value) => {
          setQuery(value);
          onSearch(value);
        },
        debounceMs: 200,
      }}
      filters={[
        { key: "plan", label: "Plan", options: PLANS, value: plan, onChange: setPlan, allLabel: "All plans" },
        { key: "status", label: "Status", options: STATUSES, value: status, onChange: setStatus, allLabel: "All statuses" },
      ]}
      chips={mine ? [{ key: "mine", label: "Owner: me", onRemove: () => setMine(false) }] : []}
      onClearAll={() => {
        setPlan("");
        setStatus("");
        setQuery("");
        setMine(false);
      }}
      actions={<button type="button">Create account</button>}
    />
  );
}

describe("FilterBar", () => {
  it("is a named search landmark with its selects and the screen's actions", () => {
    render(<Harness />);
    const bar = screen.getByRole("search", { name: "Filter accounts" });
    expect(within(bar).getByRole("combobox", { name: "Plan" })).toBeTruthy();
    expect(within(bar).getByRole("combobox", { name: "Status" })).toBeTruthy();
    expect(within(bar).getByRole("button", { name: "Create account" })).toBeTruthy();
  });

  it("shows a chip for every active filter, and removing one clears it", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const chips = screen.getByRole("list", { name: "Active filters" });
    expect(within(chips).getAllByRole("listitem").map((chip) => chip.textContent)).toEqual(["Plan: Enterprise", "Owner: me"]);

    await user.click(screen.getByRole("button", { name: "Remove filter Plan: Enterprise" }));
    expect(screen.getByRole("combobox", { name: "Plan" })).toHaveProperty("value", "");
    expect(within(screen.getByRole("list", { name: "Active filters" })).getAllByRole("listitem")).toHaveLength(1);
  });

  it("adds a chip when a select is changed", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.selectOptions(screen.getByRole("combobox", { name: "Status" }), "trial");
    expect(screen.getByRole("button", { name: "Remove filter Status: Trial" })).toBeTruthy();
  });

  it("clears everything at once, and says nothing once nothing is filtered", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole("button", { name: "Clear all" }));
    expect(screen.queryByRole("list", { name: "Active filters" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Clear all" })).toBeNull();
  });

  it("asks once the typing pauses, not on every keystroke", () => {
    vi.useFakeTimers();
    const onSearch = vi.fn();
    render(<Harness onSearch={onSearch} />);
    const box = screen.getByRole("searchbox", { name: "Search accounts" });
    fireEvent.change(box, { target: { value: "ac" } });
    fireEvent.change(box, { target: { value: "acme" } });
    act(() => vi.advanceTimersByTime(199));
    expect(onSearch).not.toHaveBeenCalled();
    act(() => vi.advanceTimersByTime(1));
    expect(onSearch).toHaveBeenCalledTimes(1);
    expect(onSearch).toHaveBeenCalledWith("acme");
  });

  it("on a phone keeps the search in the row and moves the selects into a sheet", async () => {
    stubViewport(375);
    const user = userEvent.setup();
    render(<Harness />);
    expect(screen.queryByRole("combobox", { name: "Plan" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Filters 1 active" }));
    const sheet = screen.getByRole("dialog", { name: "Filters" });
    await user.selectOptions(within(sheet).getByRole("combobox", { name: "Status" }), "active");
    expect(screen.getByRole("button", { name: "Remove filter Status: Active" })).toBeTruthy();
    await user.click(within(sheet).getByRole("button", { name: "Show results" }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});

describe("SearchInput", () => {
  it("passes value and onChange straight through when it is not debouncing", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<SearchInput label="Search deals" value="warehouse" onChange={onChange} />);
    const box = screen.getByRole("searchbox", { name: "Search deals" });
    expect(box).toHaveProperty("value", "warehouse");
    await user.type(box, "s");
    expect(onChange).toHaveBeenCalledTimes(1);
    // Controlled from outside: the caller did not update `value`, so neither did the box.
    expect(box).toHaveProperty("value", "warehouse");
  });

  it("reports an emptied box at once — clearing is a decision, not typing", () => {
    vi.useFakeTimers();
    const onDebouncedChange = vi.fn();
    render(<SearchInput label="Search" defaultValue="acme" onDebouncedChange={onDebouncedChange} />);
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "" } });
    expect(onDebouncedChange).toHaveBeenCalledWith("");
    act(() => vi.advanceTimersByTime(500));
    expect(onDebouncedChange).toHaveBeenCalledTimes(1);
  });

  it("follows the committed value when it changes from outside", () => {
    const { rerender } = render(<SearchInput label="Search" value="acme" onDebouncedChange={vi.fn()} />);
    expect(screen.getByRole("searchbox")).toHaveProperty("value", "acme");
    rerender(<SearchInput label="Search" value="" onDebouncedChange={vi.fn()} />);
    expect(screen.getByRole("searchbox")).toHaveProperty("value", "");
  });
});
