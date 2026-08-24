import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

export interface Column<T> {
  key: string;
  header: ReactNode;
  render: (row: T) => ReactNode;
  align?: "left" | "right";
  /** Extra classes on the cell — e.g. hide on small screens. */
  cellClassName?: string;
  headerClassName?: string;
  /** Sort key passed to `onSort` when the header is activated; omit for a non-sortable column. */
  sortKey?: string;
}

interface DataTableProps<T> {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T) => string;
  /** Shown in place of the body when there are no rows. */
  emptyState?: ReactNode;
  caption?: string;
  /** Makes each row activatable (click / Enter / Space) — for drill-down. */
  onRowClick?: (row: T) => void;
  /** Current sort, and a handler the sortable headers call. */
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
  sort,
  onSort,
}: DataTableProps<T>) {
  if (rows.length === 0 && emptyState) {
    return <div className="py-14">{emptyState}</div>;
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-sm">
        {caption ? <caption className="sr-only">{caption}</caption> : null}
        <thead>
          <tr className="border-b border-line">
            {columns.map((col) => {
              const sortable = col.sortKey !== undefined && onSort;
              const active = sort && col.sortKey === sort.key;
              const label =
                typeof col.header === "string" ? col.header : col.key;
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
                      onClick={() => onSort(col.sortKey!)}
                      className={cn(
                        "inline-flex items-center gap-1 rounded-md transition-colors hover:text-ink",
                        col.align === "right" ? "flex-row-reverse" : "",
                        active ? "text-ink" : "",
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
            const clickable = Boolean(onRowClick);
            return (
              <tr
                key={rowKey(row)}
                {...(clickable
                  ? {
                      tabIndex: 0,
                      role: "button",
                      onClick: () => onRowClick!(row),
                      onKeyDown: (e: React.KeyboardEvent) => {
                        if (e.key === "Enter" || e.key === " ") {
                          e.preventDefault();
                          onRowClick!(row);
                        }
                      },
                    }
                  : {})}
                className={cn(
                  "border-b border-line/70 last:border-0 transition-colors",
                  clickable
                    ? "cursor-pointer hover:bg-ground/60 focus-visible:bg-ground/60"
                    : "hover:bg-ground/60",
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
