import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { BoardIcon, HomeIcon, UsersIcon } from "../icons";
import { CommandMenu } from "./CommandMenu";
import type { CommandMenuItem, CommandMenuResults } from "./CommandMenu";

const ITEMS: CommandMenuItem[] = [
  { key: "nav-overview", label: "Overview", group: "Go to", icon: HomeIcon, onSelect: vi.fn() },
  { key: "nav-deals", label: "Deals", group: "Go to", icon: BoardIcon, onSelect: vi.fn() },
  { key: "module-hrms", label: "HRMS", group: "Modules", icon: UsersIcon, onSelect: vi.fn() },
];

describe("CommandMenu", () => {
  it("renders nothing when closed", () => {
    render(<CommandMenu open={false} onClose={vi.fn()} items={ITEMS} placeholder="Search" />);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("puts focus on the input when opened", () => {
    render(<CommandMenu open onClose={vi.fn()} items={ITEMS} placeholder="Search" />);
    expect(document.activeElement).toBe(screen.getByRole("combobox"));
  });

  it("filters the list as the query changes", async () => {
    const user = userEvent.setup();
    render(<CommandMenu open onClose={vi.fn()} items={ITEMS} placeholder="Search" />);
    await user.type(screen.getByRole("combobox"), "hrms");
    expect(screen.getByText("HRMS")).toBeTruthy();
    expect(screen.queryByText("Deals")).toBeNull();
  });

  it("runs the highlighted item and closes on Enter", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(<CommandMenu open onClose={onClose} items={ITEMS} placeholder="Search" />);
    await user.keyboard("{Enter}");
    expect(ITEMS[0]?.onSelect).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("moves the highlight with the arrow keys before running the item", async () => {
    const user = userEvent.setup();
    render(<CommandMenu open onClose={vi.fn()} items={ITEMS} placeholder="Search" />);
    await user.keyboard("{ArrowDown}{Enter}");
    expect(ITEMS[1]?.onSelect).toHaveBeenCalledTimes(1);
  });

  it("closes on Escape without running anything", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(<CommandMenu open onClose={onClose} items={ITEMS} placeholder="Search" />);
    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(ITEMS[0]?.onSelect).not.toHaveBeenCalled();
  });

  it("offers no fallback until something is typed", () => {
    render(
      <CommandMenu
        open
        onClose={vi.fn()}
        items={ITEMS}
        placeholder="Search"
        fallback={{ label: "Search deals for", onSubmit: vi.fn() }}
      />,
    );
    expect(screen.queryByText(/Search deals for/)).toBeNull();
  });

  it("runs the fallback with the typed query when nothing matches", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(
      <CommandMenu
        open
        onClose={vi.fn()}
        items={ITEMS}
        placeholder="Search"
        fallback={{ label: "Search deals for", onSubmit }}
      />,
    );
    await user.type(screen.getByRole("combobox"), "acme");
    await screen.findByText("Search deals for “acme”");
    await user.keyboard("{Enter}");
    expect(onSubmit).toHaveBeenCalledWith("acme");
  });
});

describe("CommandMenu with server results", () => {
  const RESULT: CommandMenuItem = { key: "crm-company-1", label: "Delta Corp", group: "Companies", onSelect: vi.fn() };

  function results(overrides: Partial<CommandMenuResults> = {}): CommandMenuResults {
    return { onQueryChange: vi.fn(), status: "success", items: [RESULT], partial: false, ...overrides };
  }

  it("results_render_after_static_items, and hears every trimmed query", async () => {
    const user = userEvent.setup();
    const config = results();
    render(<CommandMenu open onClose={vi.fn()} items={ITEMS} placeholder="Search" results={config} />);

    await user.type(screen.getByRole("combobox"), " de ");

    expect(config.onQueryChange).toHaveBeenLastCalledWith("de");
    const options = screen.getAllByRole("option").map((option) => option.textContent);
    expect(options).toEqual(["Deals", "Delta Corp"]);
    expect(screen.getByText("Companies")).toBeTruthy();
    await user.keyboard("{ArrowDown}{Enter}");
    expect(RESULT.onSelect).toHaveBeenCalledTimes(1);
  });

  it("status_line_is_announced: searching, partial, no matches and a hint", async () => {
    const user = userEvent.setup();
    const { rerender } = render(
      <CommandMenu open onClose={vi.fn()} items={ITEMS} placeholder="Search" results={results({ status: "loading", items: [] })} />,
    );
    await user.type(screen.getByRole("combobox"), "zz");
    expect(screen.getByRole("status").textContent).toBe("Searching…");

    rerender(<CommandMenu open onClose={vi.fn()} items={ITEMS} placeholder="Search" results={results({ partial: true })} />);
    expect(screen.getByRole("status").textContent).toBe("Some results may be missing");

    rerender(<CommandMenu open onClose={vi.fn()} items={ITEMS} placeholder="Search" results={results({ items: [] })} />);
    expect(screen.getByRole("status").textContent).toBe("No matches for “zz”.");

    rerender(
      <CommandMenu
        open
        onClose={vi.fn()}
        items={ITEMS}
        placeholder="Search"
        results={results({ status: "idle", items: [], hint: "Look up an email or phone on the Contacts screen." })}
      />,
    );
    expect(screen.getByRole("status").textContent).toBe("Look up an email or phone on the Contacts screen.");
  });

  it("without_results_behaviour_is_unchanged", async () => {
    const user = userEvent.setup();
    render(<CommandMenu open onClose={vi.fn()} items={ITEMS} placeholder="Search" />);
    await user.type(screen.getByRole("combobox"), "zz");
    expect(screen.queryByRole("status")).toBeNull();
    expect(screen.getByText("No matches for “zz”.")).toBeTruthy();
  });
});
