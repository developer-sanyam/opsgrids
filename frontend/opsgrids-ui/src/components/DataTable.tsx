import { useEffect, useId, useRef } from "react";
import type { MouseEvent, ReactNode } from "react";

import { cn } from "../lib/cn";
import { useScrollFade } from "../lib/useScrollFade";
import { TABLET_QUERY, useMediaQuery } from "../lib/useMediaQuery";
import { BulkActionBar } from "./BulkActionBar";
import { MobileDataCard } from "./MobileDataCard";
import type { MobileCardWiring } from "./MobileDataCard";
import { Skeleton } from "./States";

export interface Column<T> {
  key: string;
  header: ReactNode;
  render: (row: T) => ReactNode;
  align?: "left" | "right" | undefined;
  /** Extra classes on the cell — e.g. hide on small screens, or force tabular figures. */
  cellClassName?: string | undefined;
  headerClassName?: string | undefined;
  /** Sort key handed to `onSort` when the header is activated; omit for a fixed column. */
  sortKey?: string | undefined;
  /**
   * Where this column goes in the card `mobileCard="auto"` builds below 768px:
   *
   * - `title` — the card's name, and the name of its open control. Defaults to the first column.
   * - `status` — top right, for the badge that decides what happens next.
   * - `field` (default for the rest) — a labelled figure in the card's grid.
   * - `actions` — the card's foot, for `RowActions`.
   * - `hidden` — left out: the columns a table already hides on small screens usually belong here.
   */
  mobile?: "title" | "status" | "field" | "actions" | "hidden" | undefined;
}

export interface TableSelection<T> {
  /** The row keys currently ticked — the same strings `rowKey` returns. */
  selected: ReadonlySet<string>;
  /** One row's box changed. */
  onToggle: (key: string, next: boolean) => void;
  /**
   * The header box changed. `next` is true when it is being ticked, and the rows it applies to
   * are the ones on screen — a select-all that silently selects 4,000 rows across pages is how a
   * bulk delete goes wrong. `false` is also what the bulk-action bar's "Clear selection" sends.
   */
  onToggleAll: (next: boolean) => void;
  /** Names the row in its checkbox's label: "Select Acme Ltd". Falls back to the row key. */
  label?: ((row: T) => string) | undefined;
}

interface DataTableProps<T> {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T) => string;
  /** Shown in place of the body when there are no rows. The toolbar stays put above it. */
  emptyState?: ReactNode | undefined;
  caption?: string | undefined;
  /**
   * Search, filters — anything that acts on the table. Rendered above it, and **kept when the
   * table is empty or failed**, because a filter that returns nothing is exactly when the user
   * needs the control that will let them undo it.
   */
  toolbar?: ReactNode | undefined;
  /**
   * Called when a row is activated by click or Enter/Space. Setting it makes the whole row an
   * operable control — pointer *and* keyboard, so a drill-down is never mouse-only.
   */
  onRowClick?: ((row: T) => void) | undefined;
  /** Marks the current row in a list-plus-detail layout. Only meaningful with `onRowClick`. */
  isSelected?: ((row: T) => boolean) | undefined;
  /** Current sort, and the handler the sortable headers call. */
  sort?: { key: string; dir: "asc" | "desc" } | undefined;
  onSort?: ((key: string) => void) | undefined;
  /** Tick boxes for bulk actions. Omit it and no selection column is rendered at all. */
  selection?: TableSelection<T> | undefined;
  /**
   * What the selection can do: the buttons of a `BulkActionBar`, shown above the table once
   * anything is ticked, with the count and a Clear selection. Needs `selection`.
   */
  bulkActions?: ReactNode | undefined;
  /**
   * The body is being fetched: the header stays and skeleton rows hold the body's place, so
   * nothing jumps when the data lands. Wins over `errorState` and `emptyState` — a retry in
   * flight is loading, not failed.
   */
  loading?: boolean | undefined;
  /** How many skeleton rows `loading` draws. Defaults to the rows on screen, or 5. */
  loadingRows?: number | undefined;
  /**
   * The fetch failed: an `ErrorState` (with its Try again) in place of the body. The toolbar
   * stays, because the filter that caused a 422 is the thing to change.
   */
  errorState?: ReactNode | undefined;
  /**
   * Keep the header in view while the rows scroll. The table becomes a scroll area as tall as the
   * viewport at most, with its header pinned — for long pages of rows, not for the ten-row table
   * on a dashboard. Assumes the table sits on a surface (a `Card`), whose colour the pinned
   * header takes so rows do not show through it.
   */
  stickyHeader?: boolean | undefined;
  /**
   * Below 768px, render each row as a card instead of a table row (brief §7: no sideways-scrolling
   * tables on a phone). **The default is `"auto"`**: a `MobileDataCard` built from the columns,
   * placed by each column's `mobile`. A function renders your own, and is handed the wiring —
   * open, current, selection — to spread onto it. `"table"` opts out and keeps the table on a
   * phone, scrolling sideways inside its own region with a fade on the edge that has more.
   */
  mobileCard?: "auto" | "table" | ((row: T, wiring: MobileCardWiring) => ReactNode) | undefined;
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
  className = "size-4 cursor-pointer accent-accent",
  bare = false,
}: {
  checked: boolean;
  indeterminate: boolean;
  onChange: (next: boolean) => void;
  label: string;
  className?: string;
  /** Already inside a label that is itself the 44px target: add no wrapper of its own. */
  bare?: boolean;
}) {
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (ref.current) ref.current.indeterminate = indeterminate;
  }, [indeterminate]);
  const input = (
    <input
      ref={ref}
      type="checkbox"
      checked={checked}
      onChange={(event) => onChange(event.target.checked)}
      aria-label={label}
      className={className}
    />
  );
  // The box is 16px; the label around it is the 44px target, pulled back with a negative margin
  // so the cell keeps its padding.
  return bare ? input : <label className="-m-3 grid size-11 cursor-pointer place-items-center">{input}</label>;
}

/** A column's place in the generated card; the first column is the title unless one says otherwise. */
function placeOf<T>(column: Column<T>, columns: Column<T>[]): NonNullable<Column<T>["mobile"]> {
  if (column.mobile !== undefined) return column.mobile;
  const explicitTitle = columns.some((candidate) => candidate.mobile === "title");
  return !explicitTitle && columns[0] === column ? "title" : "field";
}

/**
 * A body row's cells — the tick-box cell and a data cell. One definition, shared by the skeleton
 * rows, so a loaded table is exactly as tall as the placeholder it replaces.
 */
const SELECT_CELL = "w-10 py-3.5 pl-3 pr-0 align-middle";
const BODY_CELL = "py-3.5 px-3 align-middle";

/** Where an empty or a failed table's message sits, in the body's place. */
const STATE_SLOT = "py-14";

/** Skeleton cells sized like text, so the rows that replace them take the same height. */
function SkeletonRows({ count, columns, selectable }: { count: number; columns: number; selectable: boolean }) {
  return Array.from({ length: count }, (_, index) => (
    <tr key={index} className="border-b border-line/70 last:border-0">
      {selectable ? (
        <td className={SELECT_CELL}>
          <Skeleton className="size-4" />
        </td>
      ) : null}
      {Array.from({ length: columns }, (_, cell) => (
        <td key={cell} className={BODY_CELL}>
          {index === 0 && cell === 0 ? <span className="sr-only">Loading</span> : null}
          <Skeleton className={cn("h-4", cell === 0 ? "w-3/4" : "w-1/2")} />
        </td>
      ))}
    </tr>
  ));
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
  bulkActions,
  loading = false,
  loadingRows,
  errorState,
  stickyHeader = false,
  mobileCard = "auto",
}: DataTableProps<T>) {
  const wide = useMediaQuery(TABLET_QUERY, true);
  const cards = mobileCard !== "table" && !wide;
  const base = useId();
  const scrollRef = useRef<HTMLDivElement>(null);
  const fade = useScrollFade(scrollRef, `${cards}-${loading}-${rows.length}-${columns.length}`);

  const selectedCount = selection
    ? rows.filter((row) => selection.selected.has(rowKey(row))).length
    : 0;
  const allSelected = selection !== undefined && rows.length > 0 && selectedCount === rows.length;
  const bulk =
    selection !== undefined && bulkActions !== undefined ? (
      <>
        {selection.selected.size > 0 ? (
          <BulkActionBar count={selection.selected.size} onClear={() => selection.onToggleAll(false)}>
            {bulkActions}
          </BulkActionBar>
        ) : null}
        {/* Always mounted, so a change of count is announced — a region that mounts with its
            text is not read reliably. */}
        <p role="status" className="sr-only">
          {selection.selected.size > 0 ? `${selection.selected.size} selected` : ""}
        </p>
      </>
    ) : null;

  if (!loading && errorState !== undefined) {
    return (
      <div className="flex flex-col gap-4">
        {toolbar}
        <div className={STATE_SLOT}>{errorState}</div>
      </div>
    );
  }

  if (!loading && rows.length === 0 && emptyState) {
    return (
      <div className="flex flex-col gap-4">
        {toolbar}
        <div className={STATE_SLOT}>{emptyState}</div>
      </div>
    );
  }

  const clickable = onRowClick !== undefined;
  const skeletonCount = loadingRows ?? (rows.length || 5);

  const cellId = (key: string, index: number) => `${base}-${key}-${index}`;

  function activate(row: T) {
    onRowClick?.(row);
  }

  function wiring(row: T): MobileCardWiring {
    const key = rowKey(row);
    return {
      ...(clickable ? { onOpen: () => activate(row) } : {}),
      ...(isSelected ? { current: isSelected(row) } : {}),
      ...(selection
        ? {
            selection: {
              checked: selection.selected.has(key),
              onChange: (next: boolean) => selection.onToggle(key, next),
              label: `Select ${selection.label?.(row) ?? key}`,
            },
          }
        : {}),
    };
  }

  function autoCard(row: T, wired: MobileCardWiring) {
    const placed = (where: NonNullable<Column<T>["mobile"]>) =>
      columns.filter((column) => placeOf(column, columns) === where);
    const title = placed("title")[0];
    const status = placed("status");
    const actions = placed("actions");
    return (
      <MobileDataCard
        {...wired}
        title={title ? title.render(row) : rowKey(row)}
        status={status.length > 0 ? status.map((column) => <span key={column.key}>{column.render(row)}</span>) : undefined}
        fields={placed("field").map((column) => ({ key: column.key, label: column.header, value: column.render(row) }))}
        actions={actions.length > 0 ? actions.map((column) => <span key={column.key}>{column.render(row)}</span>) : undefined}
      />
    );
  }

  if (cards) {
    return (
      <div className="flex flex-col gap-4">
        {toolbar}
        {bulk}
        {selection && !loading && rows.length > 0 ? (
          <label className="flex min-h-11 items-center gap-3 px-1 text-sm font-medium text-ink-soft">
            <TriStateCheckbox
              bare
              checked={allSelected}
              indeterminate={selectedCount > 0 && !allSelected}
              onChange={(next) => selection.onToggleAll(next)}
              label={allSelected ? "Clear selection" : "Select all rows on this page"}
              className="size-5 cursor-pointer accent-accent"
            />
            <span aria-hidden>{allSelected ? "Clear selection" : "Select all"}</span>
          </label>
        ) : null}
        <ul aria-label={caption} aria-busy={loading || undefined} className="flex flex-col gap-3">
          {loading
            ? Array.from({ length: skeletonCount }, (_, index) => (
                <li key={index} className="flex flex-col gap-3 rounded-card bg-surface p-4 shadow-card">
                  {index === 0 ? <span className="sr-only">Loading</span> : null}
                  <Skeleton className="h-4 w-2/3" />
                  <Skeleton className="h-3 w-1/3" />
                  <div className="grid grid-cols-2 gap-3">
                    <Skeleton className="h-8" />
                    <Skeleton className="h-8" />
                  </div>
                </li>
              ))
            : rows.map((row) => {
                const wired = wiring(row);
                return (
                  <li key={rowKey(row)}>
                    {mobileCard === "auto" ? autoCard(row, wired) : mobileCard?.(row, wired)}
                  </li>
                );
              })}
        </ul>
      </div>
    );
  }

  // A pinned header needs a scroll area of the table's own: `sticky` pins against the nearest
  // scrolling ancestor, and the sideways-scroll wrapper already is one. A plain wide table gets
  // the same treatment once it has something to scroll to: a scroll area with nothing focusable
  // in it is one a keyboard cannot move (axe's scrollable-region-focusable), so only the loading
  // skeleton — which has nothing to scroll to yet — clips instead.
  const scroller = stickyHeader
    ? { className: "scroll-fade max-h-dvh overflow-auto", tabIndex: 0, role: "region", "aria-label": caption ?? "Table" }
    : loading
      ? { className: "overflow-hidden" }
      : { className: "scroll-fade overflow-x-auto", tabIndex: 0, role: "region", "aria-label": caption ?? "Table" };
  const pinned = stickyHeader
    ? "sticky top-0 z-10 bg-surface after:absolute after:inset-x-0 after:bottom-0 after:h-px after:bg-line"
    : undefined;

  return (
    <div className="flex flex-col gap-4">
      {toolbar}
      {bulk}
      <div ref={scrollRef} data-fade={fade} {...scroller}>
        <table className="w-full border-collapse text-sm" aria-busy={loading || undefined}>
          {caption ? <caption className="sr-only">{caption}</caption> : null}
          <thead>
            <tr className="border-b border-line">
              {selection ? (
                <th scope="col" className={cn("w-10 py-3 pl-3 pr-0 text-left", pinned)}>
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
                      pinned,
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
            {loading ? (
              <SkeletonRows count={skeletonCount} columns={columns.length} selectable={selection !== undefined} />
            ) : (
              rows.map((row) => {
                const key = rowKey(row);
                const selected = isSelected?.(row) ?? false;
                const ticked = selection?.selected.has(key) ?? false;
                return (
                  // A row stays a row. Opening it is the button in its first cell, stretched over the
                  // row; a pointer click anywhere else on the row bubbles here. Controls inside the
                  // row (a link, the tick box, the actions menu) keep their own click.
                  <tr
                    key={key}
                    {...(clickable
                      ? {
                          onClick: (event: MouseEvent<HTMLTableRowElement>) => {
                            if ((event.target as Element).closest("a, button, input, select, textarea, label, [role='menu']")) return;
                            activate(row);
                          },
                        }
                      : {})}
                    className={cn(
                      "relative border-b border-line/70 last:border-0 transition-colors",
                      clickable && "cursor-pointer",
                      selected || ticked ? "bg-accent-soft/60" : "hover:bg-ground/60",
                    )}
                  >
                    {selection ? (
                      <td
                        className={SELECT_CELL}
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
                    {columns.map((col, index) => {
                      const opens = clickable && index === 0;
                      return (
                        <td
                          key={col.key}
                          id={clickable ? cellId(key, index) : undefined}
                          className={cn(
                            BODY_CELL,
                            // The cell is not the containing block, so the button covers the row.
                            opens && "static",
                            col.align === "right" ? "text-right" : "text-left",
                            col.cellClassName,
                          )}
                        >
                          {opens ? (
                            <button
                              type="button"
                              // Named by the whole row, as the row itself was: the title, then the figures
                              // and badges a screen reader needs to know which one this opens.
                              aria-labelledby={columns.map((_, at) => cellId(key, at)).join(" ")}
                              {...(isSelected ? { "aria-pressed": selected } : {})}
                              onClick={() => activate(row)}
                              className="absolute inset-0 rounded-tile focus-visible:-outline-offset-2"
                            />
                          ) : null}
                          {col.render(row)}
                        </td>
                      );
                    })}
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
