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

interface DataTableProps<T> {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T) => string;
  /** Shown in place of the body when there are no rows. */
  emptyState?: ReactNode;
  caption?: string;
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
}

/** A real semantic <table>. Header is a quiet uppercase row; body rows separate with hairlines. */
export function DataTable<T>({
  columns,
  rows,
  rowKey,
  emptyState,
  caption,
  onRowClick,
  isSelected,
  sort,
  onSort,
}: DataTableProps<T>) {
  if (rows.length === 0 && emptyState) {
    return <div className="py-14">{emptyState}</div>;
  }

  const clickable = onRowClick !== undefined;

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
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-sm">
        {caption ? <caption className="sr-only">{caption}</caption> : null}
        <thead>
          <tr className="border-b border-line">
            {columns.map((col) => {
              const sortKey = col.sortKey;
              const sortable = sortKey !== undefined && onSort !== undefined;
              const active = sort !== undefined && sortKey === sort.key;
              const label = typeof col.header === "string" ? col.header : col.key;
              return (
                <th
                  key={col.key}
                  scope="col"
                  aria-sort={
                    active ? (sort.dir === "asc" ? "ascending" : "descending") : undefined
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
            const selected = isSelected?.(row) ?? false;
            return (
              <tr
                key={rowKey(row)}
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
                  selected ? "bg-accent-soft/60" : "hover:bg-ground/60",
                )}
              >
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
  );
}
