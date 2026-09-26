import type { ReactNode } from "react";

import { cn } from "../lib/cn";

export interface ImportExportTableColumn<T> {
  key: string;
  header: string;
  render: (row: T) => ReactNode;
  /** The row's own name: a row header, so a screen reader reads it with every cell in the row. */
  rowHeader?: boolean | undefined;
  className?: string | undefined;
}

interface ImportExportTableProps<T> {
  /** Names the scrolling region: "Rows found in companies.xlsx". */
  label: string;
  columns: readonly ImportExportTableColumn<T>[];
  rows: readonly T[];
  rowKey: (row: T) => string;
}

/** A cell's text. An empty one says so, to a screen reader as well as to the eye. */
export function ImportExportCellText({ value }: { value: string | null | undefined }) {
  if (value === undefined || value === null || value === "") {
    return (
      <span className="text-faint">
        <span aria-hidden>—</span>
        <span className="sr-only">Empty</span>
      </span>
    );
  }
  return <span className="block max-w-64 break-words text-ink">{value}</span>;
}

/**
 * The scroll area's own keyboard stop. A region that scrolls and holds nothing focusable is one a
 * keyboard cannot move (axe's `scrollable-region-focusable`), the same reason `DataTable` gives
 * its scroller these.
 */
const SCROLL_REGION = { tabIndex: 0, role: "region" } as const;

const HEADER_CELL =
  "sticky top-0 z-10 bg-surface px-3 py-3 text-left text-xs font-semibold uppercase tracking-wide text-faint whitespace-nowrap " +
  "after:absolute after:inset-x-0 after:bottom-0 after:h-px after:bg-line";

/**
 * The table the import's preview and its result share: a real `<table>` in a region of its own,
 * with the header pinned while the rows scroll and a sideways scroll for the columns a phone
 * cannot fit. `DataTable` is not used because its pinned header assumes the page is the scroller;
 * inside a dialog the dialog's body is, and a header pinned to the wrong ancestor never sticks.
 */
export function ImportExportTable<T>({ label, columns, rows, rowKey }: ImportExportTableProps<T>) {
  return (
    <div
      {...SCROLL_REGION}
      aria-label={label}
      className="max-h-80 overflow-auto rounded-tile border border-line"
    >
      <table className="w-full border-collapse text-body-sm">
        <caption className="sr-only">{label}</caption>
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column.key} scope="col" className={HEADER_CELL}>
                {column.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={rowKey(row)} className="border-b border-line/70 align-top last:border-0">
              {columns.map((column) =>
                column.rowHeader === true ? (
                  <th
                    key={column.key}
                    scope="row"
                    className={cn("px-3 py-3 text-left font-normal", column.className)}
                  >
                    {column.render(row)}
                  </th>
                ) : (
                  <td key={column.key} className={cn("px-3 py-3", column.className)}>
                    {column.render(row)}
                  </td>
                ),
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
