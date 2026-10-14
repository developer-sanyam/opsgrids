/**
 * The additions a CRUD screen leans on: a toolbar that survives an empty result, a selection
 * column that does not fight the row it sits in, and sort headers that say what they are to
 * something that cannot see the arrow.
 */

import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { stubViewport } from "../test-media";
import { DataTable } from "./DataTable";
import type { Column, TableSelection } from "./DataTable";
import { MobileDataCard } from "./MobileDataCard";
import type { MobileCardWiring } from "./MobileDataCard";
import { EmptyState, ErrorState } from "./States";

interface Widget {
  id: string;
  name: string;
}

const ROWS: Widget[] = [
  { id: "w1", name: "Acme Bracket" },
  { id: "w2", name: "Bolt, M6" },
  { id: "w3", name: "Caster wheel" },
];

const COLUMNS = [
  { key: "name", header: "Name", render: (row: Widget) => row.name, sortKey: "name" },
  { key: "id", header: "Reference", render: (row: Widget) => row.id },
];

describe("DataTable", () => {
  it("keeps the toolbar when the table is empty — that is when it is needed most", () => {
    render(
      <DataTable
        columns={COLUMNS}
        rows={[]}
        rowKey={(row) => row.id}
        toolbar={<button type="button">Clear filters</button>}
        emptyState={<EmptyState title="No widgets match" description="Try a wider search." />}
      />,
    );
    expect(screen.getByRole("button", { name: "Clear filters" })).toBeTruthy();
    expect(screen.getByText("No widgets match")).toBeTruthy();
  });

  it("marks a sortable column that is not the current sort as sortable, not as sorted", () => {
    render(
      <DataTable
        columns={COLUMNS}
        rows={ROWS}
        rowKey={(row) => row.id}
        sort={{ key: "other", dir: "asc" }}
        onSort={vi.fn()}
      />,
    );
    const headers = screen.getAllByRole("columnheader");
    expect(headers[0]?.getAttribute("aria-sort")).toBe("none");
    // A column with no sortKey says nothing at all, rather than "none".
    expect(headers[1]?.hasAttribute("aria-sort")).toBe(false);
  });

  it("reports the direction of the column being sorted", async () => {
    const user = userEvent.setup();
    const onSort = vi.fn();
    render(
      <DataTable
        columns={COLUMNS}
        rows={ROWS}
        rowKey={(row) => row.id}
        sort={{ key: "name", dir: "desc" }}
        onSort={onSort}
      />,
    );
    expect(screen.getAllByRole("columnheader")[0]?.getAttribute("aria-sort")).toBe("descending");
    await user.click(screen.getByRole("button", { name: "Sort by Name" }));
    expect(onSort).toHaveBeenCalledWith("name");
  });

  it("names every tick box after the row it selects", () => {
    render(
      <DataTable
        columns={COLUMNS}
        rows={ROWS}
        rowKey={(row) => row.id}
        selection={{
          selected: new Set<string>(),
          onToggle: vi.fn(),
          onToggleAll: vi.fn(),
          label: (row) => row.name,
        }}
      />,
    );
    expect(screen.getByRole("checkbox", { name: "Select Acme Bracket" })).toBeTruthy();
    expect(screen.getByRole("checkbox", { name: "Select all rows on this page" })).toBeTruthy();
  });

  it("shows the header box as indeterminate when only some rows are ticked", () => {
    render(
      <DataTable
        columns={COLUMNS}
        rows={ROWS}
        rowKey={(row) => row.id}
        selection={{
          selected: new Set(["w1"]),
          onToggle: vi.fn(),
          onToggleAll: vi.fn(),
        }}
      />,
    );
    const all = screen.getByRole("checkbox", { name: "Select all rows on this page" });
    expect(all).toHaveProperty("indeterminate", true);
    expect(all).toHaveProperty("checked", false);
  });

  it("flips to checked, and offers to clear, once every row is ticked", () => {
    render(
      <DataTable
        columns={COLUMNS}
        rows={ROWS}
        rowKey={(row) => row.id}
        selection={{
          selected: new Set(["w1", "w2", "w3"]),
          onToggle: vi.fn(),
          onToggleAll: vi.fn(),
        }}
      />,
    );
    const all = screen.getByRole("checkbox", { name: "Clear selection" });
    expect(all).toHaveProperty("checked", true);
    expect(all).toHaveProperty("indeterminate", false);
  });

  it("selects a row without opening it", async () => {
    const user = userEvent.setup();
    const onRowClick = vi.fn();

    function Harness() {
      const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());
      return (
        <DataTable
          columns={COLUMNS}
          rows={ROWS}
          rowKey={(row) => row.id}
          onRowClick={onRowClick}
          selection={{
            selected,
            onToggle: (key, next) => {
              const copy = new Set(selected);
              if (next) copy.add(key);
              else copy.delete(key);
              setSelected(copy);
            },
            onToggleAll: vi.fn(),
            label: (row) => row.name,
          }}
        />
      );
    }
    render(<Harness />);

    await user.click(screen.getByRole("checkbox", { name: "Select Acme Bracket" }));
    expect(screen.getByRole("checkbox", { name: "Select Acme Bracket" })).toHaveProperty(
      "checked",
      true,
    );
    expect(onRowClick).not.toHaveBeenCalled();
  });

  it("still renders a plain table for the callers that pass none of this", () => {
    render(<DataTable columns={COLUMNS} rows={ROWS} rowKey={(row) => row.id} caption="Widgets" />);
    expect(screen.getAllByRole("row")).toHaveLength(4);
    expect(screen.queryByRole("checkbox")).toBeNull();
  });
});

/** The v2 additions: loading, error, bulk actions, a pinned header and cards on a phone. */
describe("DataTable v2", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function selection(selected: string[], overrides: Partial<TableSelection<Widget>> = {}): TableSelection<Widget> {
    return {
      selected: new Set(selected),
      onToggle: vi.fn(),
      onToggleAll: vi.fn(),
      label: (row) => row.name,
      ...overrides,
    };
  }

  it("keeps the header while loading, with skeleton rows in the body's place", () => {
    render(<DataTable columns={COLUMNS} rows={[]} rowKey={(row) => row.id} caption="Widgets" loading loadingRows={3} />);
    const table = screen.getByRole("table", { name: "Widgets" });
    expect(table.getAttribute("aria-busy")).toBe("true");
    expect(screen.getAllByRole("columnheader")).toHaveLength(2);
    expect(screen.getAllByRole("row")).toHaveLength(4);
    expect(screen.getByText("Loading")).toBeTruthy();
  });

  it("shows loading rather than empty or failed while a fetch is in flight", () => {
    render(
      <DataTable
        columns={COLUMNS}
        rows={[]}
        rowKey={(row) => row.id}
        loading
        emptyState={<p>No widgets</p>}
        errorState={<p>Couldn't load widgets</p>}
      />,
    );
    expect(screen.queryByText("No widgets")).toBeNull();
    expect(screen.queryByText("Couldn't load widgets")).toBeNull();
  });

  it("puts the error in the body's place and keeps the toolbar that may fix it", () => {
    render(
      <DataTable
        columns={COLUMNS}
        rows={ROWS}
        rowKey={(row) => row.id}
        toolbar={<button type="button">Clear filters</button>}
        errorState={<ErrorState message="The widget service didn't answer." onRetry={vi.fn()} />}
      />,
    );
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.getByRole("alert").textContent).toContain("The widget service didn't answer.");
    expect(screen.getByRole("button", { name: "Clear filters" })).toBeTruthy();
  });

  it("shows the bulk actions once something is ticked, and clears through onToggleAll(false)", async () => {
    const user = userEvent.setup();
    const onToggleAll = vi.fn();
    const { rerender } = render(
      <DataTable
        columns={COLUMNS}
        rows={ROWS}
        rowKey={(row) => row.id}
        selection={selection([])}
        bulkActions={<button type="button">Suspend</button>}
      />,
    );
    expect(screen.queryByRole("region", { name: "Bulk actions" })).toBeNull();
    expect(screen.getByRole("status").textContent).toBe("");

    rerender(
      <DataTable
        columns={COLUMNS}
        rows={ROWS}
        rowKey={(row) => row.id}
        selection={selection(["w1", "w3"], { onToggleAll })}
        bulkActions={<button type="button">Suspend</button>}
      />,
    );
    const bar = screen.getByRole("region", { name: "Bulk actions" });
    expect(bar.textContent).toContain("2 selected");
    expect(screen.getByRole("status").textContent).toBe("2 selected");
    await user.click(screen.getByRole("button", { name: "Clear selection" }));
    expect(onToggleAll).toHaveBeenCalledWith(false);
  });

  it("makes a pinned-header table its own keyboard-scrollable region", () => {
    render(<DataTable columns={COLUMNS} rows={ROWS} rowKey={(row) => row.id} caption="Widgets" stickyHeader />);
    const region = screen.getByRole("region", { name: "Widgets" });
    expect(region.tabIndex).toBe(0);
    expect(region.className).toContain("overflow-auto");
    expect(screen.getAllByRole("columnheader")[0]?.className).toContain("sticky");
  });

  it("is a list of cards on a phone by default", () => {
    stubViewport(375);
    render(<DataTable columns={COLUMNS} rows={ROWS} rowKey={(row) => row.id} caption="Widgets" />);
    expect(screen.queryByRole("table")).toBeNull();
    expect(within(screen.getByRole("list", { name: "Widgets" })).getAllByRole("listitem")).toHaveLength(3);
  });

  it("stays a table on a phone when it opts out, scrolling inside its own region", () => {
    stubViewport(375);
    render(<DataTable columns={COLUMNS} rows={ROWS} rowKey={(row) => row.id} caption="Widgets" mobileCard="table" />);
    expect(screen.getByRole("table")).toBeTruthy();
    const region = screen.getByRole("region", { name: "Widgets" });
    expect(region.className).toContain("overflow-x-auto");
    expect(region.className).toContain("scroll-fade");
  });

  it("keeps a clickable row a row: the open control is a button in its first cell, not the row", async () => {
    const user = userEvent.setup();
    const onRowClick = vi.fn();
    render(<DataTable columns={COLUMNS} rows={ROWS} rowKey={(row) => row.id} onRowClick={onRowClick} />);
    const row = screen.getAllByRole("row")[1] as HTMLElement;
    expect(row.getAttribute("role")).toBeNull();
    expect(row.tabIndex).toBe(-1);
    // Named by the whole row, as the row itself was.
    const open = within(row).getByRole("button", { name: "Acme Bracket w1" });
    open.focus();
    await user.keyboard("{Enter}");
    expect(onRowClick).toHaveBeenCalledTimes(1);
    // A pointer click on another cell of the row opens it too, once.
    await user.click(within(row).getByText("w1"));
    expect(onRowClick).toHaveBeenCalledTimes(2);
    expect(onRowClick).toHaveBeenLastCalledWith(ROWS[0]);
  });

  it("stays a table where the viewport cannot be measured, whatever it asked for", () => {
    render(<DataTable columns={COLUMNS} rows={ROWS} rowKey={(row) => row.id} mobileCard="auto" />);
    expect(screen.getByRole("table")).toBeTruthy();
  });

  it("becomes a list of cards on a phone, placing each column where it says", async () => {
    stubViewport(375);
    const user = userEvent.setup();
    const onRowClick = vi.fn();
    const onToggle = vi.fn();
    const columns: Column<Widget>[] = [
      { key: "name", header: "Name", render: (row) => row.name },
      { key: "id", header: "Reference", render: (row) => row.id },
      { key: "state", header: "State", render: () => "Active", mobile: "status" },
      { key: "secret", header: "Internal", render: () => "hidden value", mobile: "hidden" },
      { key: "actions", header: "Actions", render: (row) => <button type="button">Edit {row.name}</button>, mobile: "actions" },
    ];
    render(
      <DataTable
        columns={columns}
        rows={ROWS}
        rowKey={(row) => row.id}
        caption="Widgets"
        onRowClick={onRowClick}
        selection={selection([], { onToggle })}
        mobileCard="auto"
      />,
    );
    expect(screen.queryByRole("table")).toBeNull();
    const list = screen.getByRole("list", { name: "Widgets" });
    const cards = within(list).getAllByRole("listitem");
    expect(cards).toHaveLength(3);
    const first = cards[0];
    if (first === undefined) throw new Error("no card");
    expect(within(first).getByText("Reference")).toBeTruthy();
    expect(within(first).getByText("Active")).toBeTruthy();
    expect(within(first).queryByText("hidden value")).toBeNull();

    await user.click(within(first).getByRole("button", { name: "Acme Bracket" }));
    expect(onRowClick).toHaveBeenCalledWith(ROWS[0]);
    await user.click(within(first).getByRole("button", { name: "Edit Acme Bracket" }));
    expect(onRowClick).toHaveBeenCalledTimes(1);
    await user.click(within(first).getByRole("checkbox", { name: "Select Acme Bracket" }));
    expect(onToggle).toHaveBeenCalledWith("w1", true);
    expect(screen.getByRole("checkbox", { name: "Select all rows on this page" })).toBeTruthy();
  });

  it("hands a custom card the wiring to open, mark and select like its row", () => {
    stubViewport(375);
    const mobileCard = vi.fn((row: Widget, wiring: MobileCardWiring) => (
      <MobileDataCard {...wiring} title={row.name} subtitle={row.id} />
    ));
    render(
      <DataTable
        columns={COLUMNS}
        rows={ROWS}
        rowKey={(row) => row.id}
        onRowClick={vi.fn()}
        isSelected={(row) => row.id === "w2"}
        mobileCard={mobileCard}
      />,
    );
    expect(mobileCard).toHaveBeenCalledTimes(3);
    const wiring = mobileCard.mock.calls[1]?.[1];
    expect(wiring?.current).toBe(true);
    expect(typeof wiring?.onOpen).toBe("function");
    expect(screen.getByRole("button", { name: "Bolt, M6" }).getAttribute("aria-pressed")).toBe("true");
  });

  it("draws skeleton cards while a phone list loads", () => {
    stubViewport(375);
    render(<DataTable columns={COLUMNS} rows={[]} rowKey={(row) => row.id} caption="Widgets" mobileCard="auto" loading loadingRows={2} />);
    const list = screen.getByRole("list", { name: "Widgets" });
    expect(list.getAttribute("aria-busy")).toBe("true");
    expect(within(list).getAllByRole("listitem")).toHaveLength(2);
  });
});
