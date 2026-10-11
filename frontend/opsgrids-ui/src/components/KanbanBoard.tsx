import { useLayoutEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";

import { useAnnounce } from "../lib/announcer";
import { cn } from "../lib/cn";
import type { StatusTone } from "../lib/status";
import { Button } from "./Button";
import { RowActions } from "./RowActions";
import type { RowAction } from "./RowActions";

export interface KanbanColumn {
  id: string;
  label: string;
  /** The header dot. Decoration only: the label always says which column this is. */
  tone?: StatusTone | undefined;
  /** Refuses a drop. A move from the menu is still offered when `canMove` allows it. */
  closed?: boolean | undefined;
  /** A line beside the count — per-currency totals the caller formats ("₹4.2L + $8K"). */
  summary?: ReactNode | undefined;
  /** What an empty column says. Default "Nothing here". */
  emptyLabel?: string | undefined;
  /** The server's count for the column, when only some of it is loaded: "12 of 40". */
  total?: number | undefined;
}

export interface KanbanCardState {
  dragging: boolean;
  moving: boolean;
  /** The card's move menu (and any extra actions). Render it in the card; `null` when read-only. */
  actions: ReactNode;
}

export type KanbanMoveResult = "moving" | "moved" | "failed";

export interface KanbanBoardProps<T> {
  columns: readonly KanbanColumn[];
  items: readonly T[];
  getItemId: (item: T) => string;
  getColumnId: (item: T) => string;
  /** The card's contents; the board draws the card itself around them. */
  renderCard: (item: T, state: KanbanCardState) => ReactNode;
  canMove: (item: T, toColumnId: string) => boolean;
  /**
   * Awaited. The consumer applies the optimistic change and rolls it back on failure (by changing
   * `items`); a rejection only tells the board to announce the failure.
   */
  onMove: (item: T, toColumnId: string) => Promise<void>;
  /** The sentence the board's announcer says. An empty string says nothing for that result. */
  announce: (item: T, fromId: string, toId: string, result: KanbanMoveResult) => string;
  /** The card's name in its menu: "More actions for {moveMenuLabel}". */
  moveMenuLabel: (item: T) => string;
  /** Listed after the moves: Edit, Delete. */
  getExtraActions?: ((item: T) => RowAction[]) | undefined;
  /** Shown as "Show more" under a column whose `total` exceeds what is loaded. */
  onLoadMore?: ((columnId: string) => void) | undefined;
  loadingColumnIds?: ReadonlySet<string> | undefined;
  /** No menu and no drag: a board to look at. */
  readOnly?: boolean | undefined;
}

const OTHER: KanbanColumn = { id: "\u0000other", label: "Other" };

const DOT: Record<StatusTone, string> = {
  neutral: "bg-faint",
  info: "bg-info",
  success: "bg-present",
  warning: "bg-late",
  danger: "bg-danger",
};

/**
 * Cards in columns, moved by a menu on every card.
 *
 * **The menu is the move.** Every card carries a `RowActions` menu with "Move to {column}" for
 * each other column — HTML5 drag never fires on touch and is no use to a keyboard, so drag is an
 * enhancement for a pointer, never the only path. A move `canMove` refuses stays in the menu,
 * disabled with its reason, so a keyboard user learns why rather than wondering where it went.
 * `inlineLimit={1}`: with two or more actions every one sits in the menu; a card with exactly one
 * shows it as one real button.
 *
 * **Said once, focus kept.** The screen's `Announcer` hears "moving" and then the settled result;
 * the card keeps focus by id when the move remounts it in another column. A card whose column
 * the board does not know is listed under a trailing "Other" instead of being dropped or thrown on.
 */
export function KanbanBoard<T>({
  columns,
  items,
  getItemId,
  getColumnId,
  renderCard,
  canMove,
  onMove,
  announce,
  moveMenuLabel,
  getExtraActions,
  onLoadMore,
  loadingColumnIds,
  readOnly = false,
}: KanbanBoardProps<T>) {
  const say = useAnnounce();
  const [dragId, setDragId] = useState<string | null>(null);
  const [overId, setOverId] = useState<string | null>(null);
  const [movingIds, setMovingIds] = useState<ReadonlySet<string>>(new Set());
  const cards = useRef(new Map<string, HTMLElement>());
  const refocus = useRef<string | null>(null);

  const known = useMemo(() => new Set(columns.map((column) => column.id)), [columns]);
  const columnOf = (item: T) => (known.has(getColumnId(item)) ? getColumnId(item) : OTHER.id);
  const strays = items.some((item) => !known.has(getColumnId(item)));
  const shown = strays ? [...columns, OTHER] : columns;

  // A move remounts the card in another column, and the menu item that started it is gone: put
  // focus back on the card unless the person has already moved on.
  useLayoutEffect(() => {
    const id = refocus.current;
    if (id === null) return;
    const card = cards.current.get(id);
    if (card === undefined) return;
    refocus.current = null;
    const active = document.activeElement;
    if (active === null || active === document.body) card.focus();
  });

  function speak(message: string) {
    if (message !== "") say(message);
  }

  async function move(item: T, to: string) {
    const id = getItemId(item);
    const from = getColumnId(item);
    if (from === to || movingIds.has(id) || !canMove(item, to)) return;
    setMovingIds((previous) => new Set(previous).add(id));
    refocus.current = id;
    speak(announce(item, from, to, "moving"));
    let result: KanbanMoveResult = "moved";
    try {
      await onMove(item, to);
    } catch {
      result = "failed";
    }
    speak(announce(item, from, to, result));
    refocus.current = id;
    setMovingIds((previous) => {
      const next = new Set(previous);
      next.delete(id);
      return next;
    });
  }

  function actionsFor(item: T): RowAction[] {
    const current = columnOf(item);
    const moves = columns
      .filter((column) => column.id !== current)
      .map((column): RowAction => {
        const allowed = canMove(item, column.id);
        return {
          label: `Move to ${column.label}`,
          onSelect: () => void move(item, column.id),
          ...(allowed ? {} : { disabled: true, disabledReason: `This card can't move to ${column.label}.` }),
        };
      });
    return [...moves, ...(getExtraActions?.(item) ?? [])];
  }

  const dragged = dragId === null ? undefined : items.find((item) => getItemId(item) === dragId);
  const accepts = (column: KanbanColumn) =>
    !readOnly && column.id !== OTHER.id && column.closed !== true && dragged !== undefined
      && columnOf(dragged) !== column.id && canMove(dragged, column.id);

  return (
    <div
      role="group"
      aria-label="Board columns"
      // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- a scroll container must be keyboard-reachable (WCAG 2.1.1), even when no card in it takes focus.
      tabIndex={0}
      className="flex snap-x gap-4 overflow-x-auto rounded-card pb-2 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
    >
      {shown.map((column) => {
        const columnItems = items.filter((item) => columnOf(item) === column.id);
        const hasMore = column.total !== undefined && column.total > columnItems.length;
        const dropping = overId === column.id && accepts(column);
        return (
          <section
            key={column.id}
            aria-label={column.label}
            onDragOver={(event) => {
              if (!accepts(column)) return;
              event.preventDefault();
              setOverId(column.id);
            }}
            onDragLeave={(event) => {
              if (event.currentTarget === event.target) setOverId(null);
            }}
            onDrop={(event) => {
              event.preventDefault();
              if (dragged !== undefined && accepts(column)) void move(dragged, column.id);
              setOverId(null);
              setDragId(null);
            }}
            className={cn(
              "flex w-70 shrink-0 snap-start flex-col gap-3 rounded-card p-3 transition-colors",
              dropping ? "bg-accent-soft outline-2 outline-dashed outline-accent" : "bg-ground",
            )}
          >
            <header className="flex items-center gap-2 px-1">
              {column.tone !== undefined ? (
                <span aria-hidden className={cn("size-2.5 shrink-0 rounded-full", DOT[column.tone])} />
              ) : null}
              <h3 className="text-sm font-bold text-ink">{column.label}</h3>
              <span className="ml-auto flex items-center gap-2 text-xs font-semibold text-muted tnum">
                {column.summary !== undefined ? <span>{column.summary}</span> : null}
                <span>{hasMore ? `${columnItems.length} of ${column.total}` : columnItems.length}</span>
              </span>
            </header>

            {dropping ? <p className="px-1 text-xs font-semibold text-accent-ink">Drop to move here</p> : null}

            <div className="flex flex-col gap-3">
              {columnItems.map((item) => {
                const id = getItemId(item);
                const moving = movingIds.has(id);
                const dragging = dragId === id;
                const actions = readOnly ? null : (
                  <RowActions label={moveMenuLabel(item)} inlineLimit={1} actions={actionsFor(item)} />
                );
                return (
                  <article
                    key={id}
                    ref={(node) => {
                      if (node === null) cards.current.delete(id);
                      else cards.current.set(id, node);
                    }}
                    tabIndex={-1}
                    draggable={!readOnly}
                    onDragStart={readOnly ? undefined : () => setDragId(id)}
                    onDragEnd={
                      readOnly
                        ? undefined
                        : () => {
                            setDragId(null);
                            setOverId(null);
                          }
                    }
                    aria-busy={moving || undefined}
                    className={cn(
                      "flex flex-col gap-3 rounded-tile border border-line bg-surface p-3 shadow-card transition-opacity focus:outline-none focus-visible:ring-2 focus-visible:ring-accent",
                      !readOnly && "cursor-grab active:cursor-grabbing",
                      // Not faded while moving: the card stays readable at full contrast.
                      dragging && "opacity-50",
                      moving && "border-dashed border-accent",
                    )}
                  >
                    {renderCard(item, { dragging, moving, actions })}
                  </article>
                );
              })}
              {columnItems.length === 0 ? (
                <p className="rounded-tile border border-dashed border-line px-3 py-6 text-center text-xs text-faint">
                  {column.emptyLabel ?? "Nothing here"}
                </p>
              ) : null}
              {hasMore && onLoadMore !== undefined ? (
                <Button
                  variant="ghost"
                  size="sm"
                  aria-label={`Show more in ${column.label}`}
                  loading={loadingColumnIds?.has(column.id) ?? false}
                  onClick={() => onLoadMore(column.id)}
                >
                  Show more
                </Button>
              ) : null}
            </div>
          </section>
        );
      })}
    </div>
  );
}
