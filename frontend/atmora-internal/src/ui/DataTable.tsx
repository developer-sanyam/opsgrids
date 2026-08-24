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
  /**
   * Optional whole-row click (mouse convenience). Keyboard users reach the same destination through
   * a real link a column renders, so this never becomes the only path to the row's detail.
   */
  onRowClick?: (row: T) => void;
  /** Tighter row height for the console's dense cross-tenant tables. */
  dense?: boolean;
}

/** A real semantic <table>. Header is a quiet uppercase row; body rows separate with hairlines. */
export function DataTable<T>({
  columns,
  rows,
  rowKey,
  emptyState,
  caption,
  onRowClick,
  dense = false,
}: DataTableProps<T>) {
  if (rows.length === 0 && emptyState) {
    return <div className="py-10">{emptyState}</div>;
  }

  const cellPad = dense ? "py-2.5 px-3" : "py-3.5 px-3";

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
                  "py-2.5 px-3 text-xs font-semibold uppercase tracking-wide text-faint whitespace-nowrap",
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
              onClick={onRowClick ? () => onRowClick(row) : undefined}
              className={cn(
                "border-b border-line/70 last:border-0 transition-colors",
                onRowClick ? "cursor-pointer hover:bg-ground/70" : "hover:bg-ground/50",
              )}
            >
              {columns.map((col) => (
                <td
                  key={col.key}
                  className={cn(
                    cellPad,
                    "align-middle",
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
