import { useState } from "react";
import type { KeyboardEvent, PointerEvent } from "react";

/**
 * Which category of a chart is being read — under the pointer, or stepped to with the keyboard —
 * so its tooltip shows. One behaviour for the line and the bar chart.
 *
 * The keyboard half is for sighted keyboard users: the plot takes focus, Left and Right step
 * through the categories (Home, End jump), Escape puts the tooltip away, and focus arriving shows
 * the latest point. A screen reader user gets every value from the chart's data table instead.
 */
export function useChartCursor(count: number, layout: "points" | "bands") {
  const [active, setActive] = useState<number | null>(null);
  const last = count - 1;

  function clamp(index: number): number {
    return Math.min(Math.max(index, 0), last);
  }

  function onPointerMove(event: PointerEvent<HTMLElement>) {
    if (count === 0) return;
    const rect = event.currentTarget.getBoundingClientRect();
    const fraction = rect.width === 0 ? 0 : (event.clientX - rect.left) / rect.width;
    // Points sit on the edges of their slot, bands fill it: the nearest point, the band under.
    setActive(clamp(layout === "points" ? Math.round(fraction * last) : Math.floor(fraction * count)));
  }

  function onKeyDown(event: KeyboardEvent<HTMLElement>) {
    if (count === 0) return;
    const from = active ?? last;
    let next: number | null | undefined;
    if (event.key === "ArrowRight") next = clamp(from + 1);
    else if (event.key === "ArrowLeft") next = clamp(from - 1);
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = last;
    else if (event.key === "Escape") next = null;
    if (next === undefined) return;
    event.preventDefault();
    setActive(next);
  }

  return {
    active,
    handlers: {
      onPointerMove,
      onPointerLeave: () => setActive(null),
      onKeyDown,
      onFocus: () => setActive((current) => current ?? (count > 0 ? last : null)),
      onBlur: () => setActive(null),
    },
  };
}
