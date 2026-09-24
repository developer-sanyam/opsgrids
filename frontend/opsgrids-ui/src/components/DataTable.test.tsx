/**
 * The additions a CRUD screen leans on: a toolbar that survives an empty result, a selection
 * column that does not fight the row it sits in, and sort headers that say what they are to
 * something that cannot see the arrow.
 */

import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { DataTable } from "./DataTable";
import { EmptyState } from "./States";

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

  it("tightens the rows for a register, and only the rows", () => {
    const { rerender } = render(
      <DataTable columns={COLUMNS} rows={ROWS} rowKey={(row) => row.id} />,
    );
    const roomy = screen.getAllByRole("cell")[0]?.className ?? "";
    const roomyHeader = screen.getAllByRole("columnheader")[0]?.className ?? "";
    expect(roomy).toContain("py-3.5");
    expect(roomyHeader).toContain("py-3");

    rerender(<DataTable columns={COLUMNS} rows={ROWS} rowKey={(row) => row.id} dense />);
    const tight = screen.getAllByRole("cell")[0]?.className ?? "";
    const tightHeader = screen.getAllByRole("columnheader")[0]?.className ?? "";
    expect(tight).toContain("py-2.5");
    expect(tight).not.toContain("py-3.5");
    expect(tightHeader).toContain("py-2.5");
    // Same rows, same columns: density is padding and nothing else.
    expect(screen.getAllByRole("row")).toHaveLength(4);
  });

  it("makes its scroller the containing block, so hidden text in a cell cannot widen the page", () => {
    render(<DataTable columns={COLUMNS} rows={ROWS} rowKey={(row) => row.id} caption="Widgets" />);
    // jsdom has no layout, so this pins the class and not the pixels. The pixels were measured in a
    // browser at 360px: an `sr-only` "Access locked" in a column past the edge made the accounts
    // page 366px wide until the scroller was positioned, and 360px after.
    const scroller = screen.getByRole("table").parentElement;
    expect(scroller?.className).toContain("overflow-x-auto");
    expect(scroller?.className).toContain("relative");
  });
});
