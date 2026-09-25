import { useEffect, useId, useRef } from "react";
import type { KeyboardEvent, ReactNode } from "react";

import { cn } from "../lib/cn";

export interface TabItem {
  /** Stable key, handed back through `onChange`. */
  id: string;
  label: string;
  /** A count read with the label — "Users 24". Omit it rather than pass a zero nobody asked for. */
  count?: number | undefined;
  /** Present but not available. Skipped by the arrow keys, like every other disabled control. */
  disabled?: boolean | undefined;
}

export type TabsVariant = "line" | "segmented";

interface TabsProps {
  /** Names the tab list for a screen reader: "Account sections", "Trend range". */
  label: string;
  items: TabItem[];
  /** The selected tab's `id`. */
  value: string;
  onChange: (id: string) => void;
  /**
   * `line` (default): a page's or a card's sections, underlined, scrolling sideways on a phone
   * rather than wrapping. `segmented`: a small set of views over the same content — a chart's
   * range — as one rounded control.
   */
  variant?: TabsVariant | undefined;
  /**
   * `automatic` (default): the arrow keys select as they move, which is right when a panel is
   * already in memory. `manual`: arrows only move focus and Enter or Space selects — for panels
   * that fetch, so walking past three tabs does not start three requests.
   */
  activation?: "automatic" | "manual" | undefined;
  /**
   * The selected tab's panel. Rendered inside a `tabpanel` tied to the tab, so a screen reader
   * says which tab the content belongs to. Leave it out only when the tabs drive something
   * rendered elsewhere, and then the tabs control nothing ARIA can point at.
   */
  children?: ReactNode | undefined;
  className?: string | undefined;
}

const LIST: Record<TabsVariant, string> = {
  // `overflow-x-auto` is the mobile behaviour the brief asks for (§2): the row scrolls, it never
  // wraps into a second line of tabs that reads as a second control.
  // `relative` so each tab's `offsetLeft` is measured from the row, for `reveal` below.
  line: "relative flex max-w-full gap-1 overflow-x-auto border-b border-line",
  segmented: "relative inline-flex max-w-full gap-1 overflow-x-auto rounded-full bg-ground p-1",
};

const TAB: Record<TabsVariant, { base: string; on: string; off: string }> = {
  line: {
    base:
      "relative inline-flex h-11 shrink-0 items-center gap-2 px-3 text-sm font-semibold whitespace-nowrap " +
      "transition-colors duration-150 after:absolute after:inset-x-3 after:bottom-0 after:h-0.5 after:rounded-full",
    on: "text-ink after:bg-accent",
    off: "text-muted hover:text-ink after:bg-transparent",
  },
  segmented: {
    base:
      "inline-flex h-9 shrink-0 items-center gap-2 rounded-full px-3 text-sm font-semibold whitespace-nowrap " +
      "transition-colors duration-150",
    on: "bg-surface text-ink shadow-card",
    off: "text-muted hover:text-ink",
  },
};

/**
 * Tabs: one tab stop for the whole row, arrow keys between tabs, the selected panel below.
 *
 * **Roving focus.** Only the selected tab is in the Tab order, so a keyboard user passes the row
 * in one press instead of one per tab. Left and Right move between tabs and wrap; Home and End
 * jump to the ends; disabled tabs are skipped. The tab that takes focus is scrolled into view, so
 * the row can overflow a phone's width without hiding the tab the keyboard is on.
 *
 * **Not navigation.** Tabs switch content in place. A row of links to other routes is a nav with
 * `aria-current="page"`, not a tab list, and does not use this.
 */
export function Tabs({
  label,
  items,
  value,
  onChange,
  variant = "line",
  activation = "automatic",
  children,
  className,
}: TabsProps) {
  const base = useId();
  const listRef = useRef<HTMLDivElement>(null);
  const tabId = (id: string) => `${base}-tab-${id}`;
  const panelId = `${base}-panel`;
  const hasPanel = children !== undefined;

  // The tab in the Tab order: the selected one, or the first usable one if the selection is
  // disabled or unknown — a row with no reachable tab is a row a keyboard cannot enter.
  const enabled = items.filter((item) => item.disabled !== true);
  const selectedEnabled = enabled.some((item) => item.id === value);
  const tabStop = selectedEnabled ? value : enabled[0]?.id;

  function tabNode(id: string): HTMLButtonElement | undefined {
    const tabs = listRef.current?.querySelectorAll<HTMLButtonElement>("[role='tab']") ?? [];
    return Array.from(tabs).find((tab) => tab.dataset["tabId"] === id);
  }

  function focusTab(id: string) {
    const node = tabNode(id);
    node?.focus();
    if (node) reveal(node);
  }

  /**
   * Scroll the row — only the row, never the page — so a tab is wholly in view. The selected tab
   * is revealed on arrival too: a deep link to the ninth section of an account opens with that
   * tab showing, not scrolled off a phone's edge.
   */
  function reveal(node: HTMLElement) {
    const list = listRef.current;
    if (!list) return;
    const start = node.offsetLeft;
    const end = start + node.offsetWidth;
    if (start < list.scrollLeft) list.scrollLeft = start;
    else if (end > list.scrollLeft + list.clientWidth) list.scrollLeft = end - list.clientWidth;
  }

  useEffect(() => {
    const node = tabNode(value);
    if (node) reveal(node);
  }, [value]);

  function onKeyDown(event: KeyboardEvent<HTMLButtonElement>, current: string) {
    const index = enabled.findIndex((item) => item.id === current);
    if (index === -1 || enabled.length === 0) return;
    let next: TabItem | undefined;
    if (event.key === "ArrowRight") next = enabled[(index + 1) % enabled.length];
    else if (event.key === "ArrowLeft") next = enabled[(index - 1 + enabled.length) % enabled.length];
    else if (event.key === "Home") next = enabled[0];
    else if (event.key === "End") next = enabled[enabled.length - 1];
    if (next === undefined) return;
    event.preventDefault();
    focusTab(next.id);
    if (activation === "automatic" && next.id !== value) onChange(next.id);
  }

  return (
    <div className={cn("flex min-w-0 flex-col gap-4", className)}>
      <div
        ref={listRef}
        role="tablist"
        aria-label={label}
        aria-orientation="horizontal"
        className={LIST[variant]}
      >
        {items.map((item) => {
          const selected = item.id === value;
          const style = TAB[variant];
          return (
            <button
              key={item.id}
              id={tabId(item.id)}
              type="button"
              role="tab"
              data-tab-id={item.id}
              aria-selected={selected}
              aria-controls={selected && hasPanel ? panelId : undefined}
              tabIndex={item.id === tabStop ? 0 : -1}
              disabled={item.disabled === true}
              onClick={() => {
                if (!selected) onChange(item.id);
              }}
              onKeyDown={(event) => onKeyDown(event, item.id)}
              className={cn(
                style.base,
                selected ? style.on : style.off,
                // Drawn inside the tab: the row scrolls, so an outline outside it would be clipped.
                "focus-visible:-outline-offset-2",
                "disabled:cursor-not-allowed disabled:opacity-60",
              )}
            >
              {item.label}
              {item.count !== undefined ? (
                <span
                  className={cn(
                    "tnum rounded-full px-2 py-0.5 text-xs font-semibold",
                    selected ? "bg-accent-soft text-accent-ink" : "bg-ground text-muted",
                  )}
                >
                  {item.count}
                </span>
              ) : null}
            </button>
          );
        })}
      </div>
      {hasPanel ? (
        <div
          id={panelId}
          role="tabpanel"
          aria-labelledby={tabId(value)}
          // The panel is a stop of its own, per the WAI-ARIA tabs pattern, so a keyboard user can
          // reach content that has no control of its own in it.
          tabIndex={0}
          className="min-w-0 rounded-tile"
        >
          {children}
        </div>
      ) : null}
    </div>
  );
}
