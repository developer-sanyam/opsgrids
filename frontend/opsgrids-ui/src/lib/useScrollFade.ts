import { useEffect, useState } from "react";
import type { RefObject } from "react";

export type ScrollFade = "none" | "start" | "end" | "both";

/**
 * Which edges of a sideways-scrolling element still have something to scroll to, kept current as
 * it scrolls, resizes and its content changes. Feed it to `data-fade` on an element with the
 * `scroll-fade` class (`console.css`); `none` is returned for an element that fits, so a short
 * row gets no fade. `watch` is anything whose change means the element was swapped or refilled.
 */
export function useScrollFade(ref: RefObject<HTMLElement | null>, watch?: unknown): ScrollFade {
  const [fade, setFade] = useState<ScrollFade>("none");

  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    const measure = () => {
      const max = node.scrollWidth - node.clientWidth;
      if (max <= 1) {
        setFade("none");
        return;
      }
      const atStart = node.scrollLeft <= 1;
      const atEnd = node.scrollLeft >= max - 1;
      setFade(atStart ? "end" : atEnd ? "start" : "both");
    };
    measure();
    node.addEventListener("scroll", measure, { passive: true });
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    observer?.observe(node);
    for (const child of Array.from(node.children)) observer?.observe(child);
    return () => {
      node.removeEventListener("scroll", measure);
      observer?.disconnect();
    };
  }, [ref, watch]);

  return fade;
}
