import { useCallback, useLayoutEffect, useRef, useState } from "react";
import type { ReactNode } from "react";

import { cn } from "../lib/cn";

export type TimelineTone = "neutral" | "accent" | "positive" | "negative" | "late" | "info";

const DOT_TONE: Record<TimelineTone, string> = {
  neutral: "bg-faint",
  accent: "bg-accent",
  positive: "bg-present",
  negative: "bg-negative",
  late: "bg-late",
  info: "bg-info",
};

export interface TimelineEntry {
  id: string;
  /** One sentence, in the product's voice. The dot never carries meaning the sentence doesn't. */
  content: ReactNode;
  tone?: TimelineTone | undefined;
  /** The rendered instant — `15 Sep, 6:40 pm IST · 2 days ago`. */
  timestamp?: ReactNode | undefined;
  /** The UTC instant for the `<time datetime>` attribute, when `timestamp` is set. */
  dateTime?: string | undefined;
  /** Quoted verbatim beneath the sentence — a decider's comment, never summarised. */
  quote?: string | undefined;
}

interface TimelineProps {
  /** Chronological, oldest first: the history reads as a story. */
  entries: TimelineEntry[];
  /**
   * Collapse to the most recent N when there are more. An escalation ladder can reach 32 rungs,
   * and the recent end is the one being read.
   */
  collapseAfter?: number | undefined;
  /** "Show all 14 events" — the noun is the caller's, since a chain shows "levels". */
  expandLabel?: ((total: number) => string) | undefined;
  /** Rendered instead of a blank card when the history is empty. */
  emptyMessage?: string | undefined;
  className?: string | undefined;
}

/**
 * An ordered history: tone dot, sentence, timestamp.
 *
 * A real `<ol>`, so the order is in the markup rather than in the styling, and each timestamp is a
 * `<time datetime>` carrying the UTC instant while its text carries the location's zone. Expanding
 * a collapsed history moves focus to the first newly revealed entry — otherwise a keyboard user
 * presses the button and lands nowhere.
 */
export function Timeline({
  entries,
  collapseAfter,
  expandLabel = (total) => `Show all ${total} events`,
  emptyMessage = "Nothing has happened yet.",
  className,
}: TimelineProps) {
  const [expanded, setExpanded] = useState(false);
  const firstRevealedRef = useRef<HTMLLIElement>(null);
  const shouldFocusRef = useRef(false);

  // `collapseAfter={0}` is not "collapse to nothing with no way back" — the most recent zero
  // entries is not a state worth building, so it collapses the same as leaving the prop unset.
  const collapsed =
    collapseAfter !== undefined &&
    collapseAfter > 0 &&
    !expanded &&
    entries.length > collapseAfter;
  const hiddenCount = collapsed ? entries.length - collapseAfter : 0;

  const expand = useCallback(() => {
    shouldFocusRef.current = true;
    setExpanded(true);
  }, []);

  useLayoutEffect(() => {
    if (!shouldFocusRef.current) return;
    shouldFocusRef.current = false;
    firstRevealedRef.current?.focus();
  }, [expanded]);

  if (entries.length === 0) {
    return <p className="text-sm text-muted">{emptyMessage}</p>;
  }

  return (
    <div className={cn("flex flex-col gap-3", className)}>
      {collapsed ? (
        <button
          type="button"
          onClick={expand}
          aria-expanded={expanded}
          className="self-start rounded-tile text-sm font-semibold text-accent-ink transition-colors hover:text-accent"
        >
          {expandLabel(entries.length)}
        </button>
      ) : null}
      <ol className="flex flex-col gap-4">
        {entries.map((entry, index) => {
          const isHidden = collapsed && index < hiddenCount;
          if (isHidden) return null;
          const revealed = expanded && index === 0;
          return (
            <li
              key={entry.id}
              ref={revealed ? firstRevealedRef : undefined}
              tabIndex={revealed ? -1 : undefined}
              className="flex gap-3 focus:outline-none"
            >
              <span
                aria-hidden
                className={cn(
                  "mt-1.5 size-2 shrink-0 rounded-full",
                  DOT_TONE[entry.tone ?? "neutral"],
                )}
              />
              <div className="flex min-w-0 flex-col gap-1 leading-snug">
                <p className="text-sm text-ink-soft">{entry.content}</p>
                {entry.quote ? (
                  <blockquote className="border-l-2 border-line pl-3 text-sm text-muted">
                    {entry.quote}
                  </blockquote>
                ) : null}
                {entry.timestamp ? (
                  <time
                    {...(entry.dateTime !== undefined ? { dateTime: entry.dateTime } : {})}
                    className="text-xs text-faint"
                  >
                    {entry.timestamp}
                  </time>
                ) : null}
              </div>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
