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
}

interface DataTableProps<T> {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T) => string;
  /** Shown in place of the body when there are no rows. */
  emptyState?: ReactNode;
  caption?: string;
  /** Called when a row is activated (click / Enter). Rows become buttons when set. */
  onRowClick?: (row: T) => void;
}

/** A real semantic <table>. Header is a quiet uppercase row; body rows separate with hairlines. */
export function DataTable<T>({
  columns,
  rows,
  rowKey,
  emptyState,
  caption,
  onRowClick,
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
            {columns.map((col) => (
              <th
                key={col.key}
                scope="col"
                className={cn(
                  "py-3 px-3 text-xs font-semibold uppercase tracking-wide text-faint whitespace-nowrap",
                  col.align === "right" ? "text-right" : "text-left",
                  col.headerClassName,
                )}
              >
                {col.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr
              key={rowKey(row)}
              className={cn(
                "border-b border-line/70 last:border-0 transition-colors hover:bg-ground/60",
                onRowClick &&
                  "cursor-pointer focus-within:bg-ground/60",
              )}
              {...(onRowClick
                ? {
                    onClick: () => onRowClick(row),
                    tabIndex: 0,
                    onKeyDown: (e: React.KeyboardEvent) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        onRowClick(row);
                      }
                    },
                  }
                : {})}
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
          ))}
        </tbody>
      </table>
    </div>
  );
}
