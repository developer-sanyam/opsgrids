import { useEffect, useState } from "react";
import type { RefObject } from "react";

/**
 * The measured width of an element, in px.
 *
 * The store canvas stacks below a width, and that width is the *canvas's*, not the viewport's — a
 * 390px device preview inside a wide browser has to stack. A media query cannot express that, so
 * the canvas is measured.
 *
 * Returns `fallback` until the first measurement, so the first paint reserves the same layout the
 * measured one will use and nothing shifts. Environments without `ResizeObserver` (jsdom, and any
 * server render) keep the fallback, which is why every caller passes a sensible one.
 */
export function useElementWidth(ref: RefObject<HTMLElement | null>, fallback: number): number {
  const [width, setWidth] = useState(fallback);

  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    if (typeof ResizeObserver === "undefined") {
      setWidth(element.getBoundingClientRect().width || fallback);
      return;
    }
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (entry) setWidth(entry.contentRect.width);
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [ref, fallback]);

  return width;
}
