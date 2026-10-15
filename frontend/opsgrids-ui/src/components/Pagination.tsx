import { useEffect, useId, useRef } from "react";

import { cn } from "../lib/cn";
import { useAnnounce } from "../lib/announcer";
import { ChevronDown, ChevronRight } from "../icons";

interface PaginationProps {
  /** 1-based, straight from the server envelope's `page`. */
  page: number;
  /** The envelope's `pages`. `0` for an empty result — the server sends that, not `1`. */
  pages: number;
  /** The envelope's `total`: rows matching the query, not rows on screen. */
  total: number;
  /** The envelope's `size`. */
  size: number;
  onPageChange: (page: number) => void;
  /** Omit to render a fixed page size with no control. */
  onSizeChange?: ((size: number) => void) | undefined;
  sizeOptions?: number[] | undefined;
  /** What is being counted, plural: "widgets", "suppliers". */
  noun: string;
  /** The same noun for exactly one ("widget"), so "of 1 widgets" never reads wrongly. */
  singularNoun?: string | undefined;
  className?: string | undefined;
}

/** How many numbered buttons sit either side of the current page before an ellipsis takes over. */
const WINDOW = 1;

/**
 * The page numbers to render. Always includes the first and last page, so the two ends of a long
 * list are one click away rather than a hold on Next.
 *
 * A gap is the negative of the page it follows — `-3` is the ellipsis after page 3 — which gives
 * every entry a stable identity of its own rather than one borrowed from its array index.
 */
function pageList(page: number, pages: number): number[] {
  if (pages <= 7) return Array.from({ length: pages }, (_, index) => index + 1);
  const wanted = new Set<number>([1, pages, page]);
  for (let offset = 1; offset <= WINDOW; offset += 1) {
    if (page - offset > 1) wanted.add(page - offset);
    if (page + offset < pages) wanted.add(page + offset);
  }
  const sorted = [...wanted].sort((a, b) => a - b);
  const out: number[] = [];
  let previous = 0;
  for (const value of sorted) {
    if (previous !== 0 && value - previous > 1) out.push(-previous);
    out.push(value);
    previous = value;
  }
  return out;
}

/**
 * Numbered paging for a server-paged list — the `{items, total, page, size, pages}` envelope
 * `platform_core.pagination` returns.
 *
 * `LoadMore` is the other half of this decision and is still the right one for a queue somebody
 * works through top to bottom: it appends, and its closing line tells them they reached the end.
 * This is for a list somebody *navigates* — jumping to page 9, changing the page size, coming
 * back to where they were after an edit. A table with sortable columns almost always wants this
 * one, because "load more" and "sort by name" contradict each other.
 *
 * The count line is not decoration: it is the only thing on the screen that says how many rows
 * the filter actually matched, and it is announced on every page change so that a keyboard user
 * who pressed Next knows the table under them was replaced rather than extended.
 */
export function Pagination({
  page,
  pages,
  total,
  size,
  onPageChange,
  onSizeChange,
  sizeOptions = [25, 50, 100],
  noun,
  singularNoun,
  className,
}: PaginationProps) {
  const announce = useAnnounce();
  const sizeId = useId();
  const previousPage = useRef(page);

  const first = total === 0 ? 0 : (page - 1) * size + 1;
  const last = Math.min(page * size, total);
  const summary =
    total === 0
      ? `No ${noun}`
      : `Showing ${first}–${last} of ${total} ${total === 1 && singularNoun !== undefined ? singularNoun : noun}`;

  useEffect(() => {
    if (page === previousPage.current) return;
    previousPage.current = page;
    announce(`Page ${page} of ${pages}. ${summary}.`);
  }, [page, pages, summary, announce]);

  const numbers = pages > 1 ? pageList(page, pages) : [];

  return (
    <div
      className={cn(
        "flex flex-col gap-3 pt-2 sm:flex-row sm:items-center sm:justify-between",
        className,
      )}
    >
      <p className="tnum text-sm text-muted">{summary}</p>

      <div className="flex items-center gap-4">
        {onSizeChange ? (
          <div className="flex items-center gap-2">
            <label htmlFor={sizeId} className="text-body-sm font-medium text-muted">
              Per page
            </label>
            <div className="relative">
              <select
                id={sizeId}
                value={size}
                onChange={(event) => onSizeChange(Number(event.target.value))}
                className={cn(
                  "tnum h-11 cursor-pointer appearance-none rounded-tile border border-field-edge bg-surface md:h-9",
                  "pl-3 pr-8 text-sm text-ink transition-colors hover:border-muted",
                  "focus:outline-none focus-visible:outline-2 focus-visible:outline-focus focus-visible:outline-offset-2",
                )}
              >
                {sizeOptions.map((option) => (
                  <option key={option} value={option}>
                    {option}
                  </option>
                ))}
              </select>
              <ChevronDown
                className="pointer-events-none absolute right-3 top-1/2 size-4 -translate-y-1/2 text-faint"
                aria-hidden
              />
            </div>
          </div>
        ) : null}

        {pages > 1 ? (
          <nav aria-label={`${noun} pages`} className="flex items-center gap-1 max-md:gap-2">
            <button
              type="button"
              onClick={() => onPageChange(page - 1)}
              disabled={page <= 1}
              aria-label="Previous page"
              className="grid size-11 shrink-0 place-items-center md:size-9 rounded-tile text-ink-soft transition-colors hover:bg-sunken disabled:cursor-not-allowed disabled:text-disabled disabled:hover:bg-transparent"
            >
              <ChevronRight className="size-4 rotate-180" />
            </button>

            {/* A phone has room for Previous, where you are, and Next, each a full 44px target; the
                numbered row (and its seven small buttons) is from 768px. */}
            <span className="tnum px-2 text-sm font-medium text-ink-soft md:hidden">
              Page {page} of {pages}
            </span>

            {numbers.map((value) =>
              value < 0 ? (
                <span key={`gap${value}`} aria-hidden className="hidden px-1 text-sm text-faint md:inline">
                  …
                </span>
              ) : (
                <button
                  key={value}
                  type="button"
                  onClick={() => onPageChange(value)}
                  aria-label={`Page ${value}`}
                  aria-current={value === page ? "page" : undefined}
                  className={cn(
                    "tnum hidden h-9 min-w-9 place-items-center rounded-tile px-2 text-sm transition-colors md:grid",
                    value === page
                      ? "bg-accent font-bold text-on-accent"
                      : "font-medium text-ink-soft hover:bg-sunken",
                  )}
                >
                  {value}
                </button>
              ),
            )}

            <button
              type="button"
              onClick={() => onPageChange(page + 1)}
              disabled={page >= pages}
              aria-label="Next page"
              className="grid size-11 shrink-0 place-items-center md:size-9 rounded-tile text-ink-soft transition-colors hover:bg-sunken disabled:cursor-not-allowed disabled:text-disabled disabled:hover:bg-transparent"
            >
              <ChevronRight className="size-4" />
            </button>
          </nav>
        ) : null}
      </div>
    </div>
  );
}
