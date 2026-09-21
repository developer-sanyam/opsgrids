import { useEffect, useRef } from "react";
import type { KeyboardEvent, ReactNode } from "react";

import { cn } from "../lib/cn";

export interface Column<T> {
  key: string;
  header: ReactNode;
  render: (row: T) => ReactNode;
  align?: "left" | "right";
  /** Extra classes on the cell — e.g. hide on small screens, or force tabular figures. */
  cellClassName?: string;
  headerClassName?: string;
  /** Sort key handed to `onSort` when the header is activated; omit for a fixed column. */
  sortKey?: string;
}

export interface TableSelection<T> {
  /** The row keys currently ticked — the same strings `rowKey` returns. */
  selected: ReadonlySet<string>;
  /** One row's box changed. */
  onToggle: (key: string, next: boolean) => void;
  /**
   * The header box changed. `next` is true when it is being ticked, and the rows it applies to
   * are the ones on screen — a select-all that silently selects 4,000 rows across pages is how a
   * bulk delete goes wrong.
   */
  onToggleAll: (next: boolean) => void;
  /** Names the row in its checkbox's label: "Select Acme Ltd". Falls back to the row key. */
  label?: (row: T) => string;
}

interface DataTableProps<T> {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T) => string;
  /** Shown in place of the body when there are no rows. The toolbar stays put above it. */
  emptyState?: ReactNode;
  caption?: string;
  /**
   * Search, filters, a bulk-action bar — anything that acts on the table. Rendered above it, and
   * **kept when the table is empty**, because a filter that returns nothing is exactly when the
   * user needs the control that will let them undo it.
   */
  toolbar?: ReactNode;
  /**
   * Called when a row is activated by click or Enter/Space. Setting it makes the whole row an
   * operable control — pointer *and* keyboard, so a drill-down is never mouse-only.
   */
  onRowClick?: (row: T) => void;
  /** Marks the current row in a list-plus-detail layout. Only meaningful with `onRowClick`. */
  isSelected?: (row: T) => boolean;
  /** Current sort, and the handler the sortable headers call. */
  sort?: { key: string; dir: "asc" | "desc" };
  onSort?: (key: string) => void;
  /** Tick boxes for bulk actions. Omit it and no selection column is rendered at all. */
  selection?: TableSelection<T>;
}

/**
 * The header tick box, which has three states and only two of them are a boolean.
 *
 * `indeterminate` is not an attribute — it exists only as a DOM property — so it has to be set
 * on the node. Without it "some rows selected" and "no rows selected" look identical, and the
 * next click does the opposite of what the box appears to promise.
 */
function TriStateCheckbox({
  checked,
  indeterminate,
  onChange,
  label,
}: {
  checked: boolean;
  indeterminate: boolean;
  onChange: (next: boolean) => void;
  label: string;
}) {
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (ref.current) ref.current.indeterminate = indeterminate;
  }, [indeterminate]);
  return (
    <input
      ref={ref}
      type="checkbox"
      checked={checked}
      onChange={(event) => onChange(event.target.checked)}
      aria-label={label}
      className="size-4 cursor-pointer accent-accent"
    />
  );
}

/** A real semantic <table>. Header is a quiet uppercase row; body rows separate with hairlines. */
export function DataTable<T>({
  columns,
  rows,
  rowKey,
  emptyState,
  caption,
  toolbar,
  onRowClick,
  isSelected,
  sort,
  onSort,
  selection,
}: DataTableProps<T>) {
  if (rows.length === 0 && emptyState) {
    return (
      <div className="flex flex-col gap-4">
        {toolbar}
        <div className="py-14">{emptyState}</div>
      </div>
    );
  }

  const clickable = onRowClick !== undefined;
  const selectedCount = selection
    ? rows.filter((row) => selection.selected.has(rowKey(row))).length
    : 0;
  const allSelected = selection !== undefined && rows.length > 0 && selectedCount === rows.length;

  function activate(row: T) {
    onRowClick?.(row);
  }

  function onRowKeyDown(event: KeyboardEvent<HTMLTableRowElement>, row: T) {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      activate(row);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      {toolbar}
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-sm">
          {caption ? <caption className="sr-only">{caption}</caption> : null}
          <thead>
            <tr className="border-b border-line">
              {selection ? (
                <th scope="col" className="w-10 py-3 pl-3 pr-0 text-left">
                  <TriStateCheckbox
                    checked={allSelected}
                    indeterminate={selectedCount > 0 && !allSelected}
                    onChange={(next) => selection.onToggleAll(next)}
                    label={allSelected ? "Clear selection" : "Select all rows on this page"}
                  />
                </th>
              ) : null}
              {columns.map((col) => {
                const sortKey = col.sortKey;
                const sortable = sortKey !== undefined && onSort !== undefined;
                const active = sort !== undefined && sortKey === sort.key;
                const label = typeof col.header === "string" ? col.header : col.key;
                return (
                  <th
                    key={col.key}
                    scope="col"
                    // Every sortable column says so, not only the one in use: `aria-sort` is
                    // absent on a column that cannot be sorted and `none` on one that can, which
                    // is how a screen reader tells "sortable, not sorted" from "not sortable".
                    aria-sort={
                      active
                        ? sort.dir === "asc"
                          ? "ascending"
                          : "descending"
                        : sortable
                          ? "none"
                          : undefined
                    }
                    className={cn(
                      "py-3 px-3 text-xs font-semibold uppercase tracking-wide text-faint whitespace-nowrap",
                      col.align === "right" ? "text-right" : "text-left",
                      col.headerClassName,
                    )}
                  >
                    {sortable ? (
                      <button
                        type="button"
                        onClick={() => onSort(sortKey)}
                        className={cn(
                          "inline-flex items-center gap-1 rounded-md transition-colors hover:text-ink",
                          col.align === "right" && "flex-row-reverse",
                          active && "text-ink",
                        )}
                        aria-label={`Sort by ${label}`}
                      >
                        {col.header}
                        <span aria-hidden className="text-[0.7rem]">
                          {active ? (sort.dir === "asc" ? "▲" : "▼") : "↕"}
                        </span>
                      </button>
                    ) : (
                      col.header
                    )}
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const key = rowKey(row);
              const selected = isSelected?.(row) ?? false;
              const ticked = selection?.selected.has(key) ?? false;
              return (
                <tr
                  key={key}
                  {...(clickable
                    ? {
                        role: "button",
                        tabIndex: 0,
                        onClick: () => activate(row),
                        onKeyDown: (event: KeyboardEvent<HTMLTableRowElement>) =>
                          onRowKeyDown(event, row),
                        ...(isSelected ? { "aria-pressed": selected } : {}),
                      }
                    : {})}
                  className={cn(
                    "border-b border-line/70 last:border-0 transition-colors",
                    clickable && "cursor-pointer focus-visible:bg-ground/60",
                    selected || ticked ? "bg-accent-soft/60" : "hover:bg-ground/60",
                  )}
                >
                  {selection ? (
                    <td
                      className="w-10 py-3.5 pl-3 pr-0 align-middle"
                      // The tick box is its own control inside a row that may itself be one.
                      // Without this, selecting a row for a bulk delete also opens it.
                      onClick={(event) => event.stopPropagation()}
                      onKeyDown={(event) => event.stopPropagation()}
                    >
                      <input
                        type="checkbox"
                        checked={ticked}
                        onChange={(event) => selection.onToggle(key, event.target.checked)}
                        aria-label={`Select ${selection.label?.(row) ?? key}`}
                        className="size-4 cursor-pointer accent-accent"
                      />
                    </td>
                  ) : null}
                  {columns.map((col) => (
                    <td
                      key={col.key}
                      className={cn(
                        "py-3.5 px-3 align-middle",
                        col.align === "right" ? "text-right" : "text-left",
                        col.cellClassName,
                      )}
                    >
                      {col.render(row)}
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
