import { useEffect, useRef, useState } from "react";
import type { RefObject } from "react";

/**
 * The measured width of an element, in px.
 *
 * The store canvas stacks below a width, and that width is the *canvas's*, not the viewport's — a
 * 390px device preview inside a wide browser has to stack. A media query cannot express that, so
 * the canvas is measured.
 *
 * Returns `fallback` until the first measurement, so the first paint reserves the same layout the
 * measured one will use and nothing shifts. It also keeps *following* `fallback` until then, which
 * the editor's canvas relies on: its fallback is the device toggle's design width, so a caller
 * whose fallback changes before anything is measurable must see the change and not the value it
 * mounted with.
 *
 * **The element is frequently absent on the render that starts the measurement.** Callers put this
 * behind an async state — the live route paints a skeleton first and mounts the measured frame only
 * once the store resolves — and an effect keyed on `[ref, fallback]` runs once, finds `ref.current`
 * null and never runs again. That froze the width at the fallback for the life of the page: with
 * `window.innerWidth`-at-mount baked into every stacking decision the section library makes, a page
 * loaded wide and then narrowed kept its desktop layout, and a phone that shrinks a page to fit
 * measured the visual viewport rather than the layout one. Hence two effects below rather than one.
 */
export function useElementWidth(ref: RefObject<HTMLElement | null>, fallback: number): number {
  /** Null until something has actually been measured, so an unmeasured hook tracks `fallback`. */
  const [measured, setMeasured] = useState<number | null>(null);
  /** The element the live observer is attached to, so re-running the attach step is a no-op. */
  const attached = useRef<HTMLElement | null>(null);
  const observer = useRef<ResizeObserver | null>(null);

  // Measure now, for the element that is already there. A zero width means a `display: none`
  // ancestor rather than a zero-wide element — and is what every element reports under jsdom — so
  // the fallback stands in that case.
  useEffect(() => {
    const element = ref.current;
    if (element) setMeasured(element.getBoundingClientRect().width || fallback);
  }, [ref, fallback]);

  // Keep measuring. Deliberately dependency-free: React offers no signal that a ref has been
  // populated, and an element can only appear as the result of a render, so "after every render" is
  // precisely the set of moments at which that can have happened. The identity check makes the
  // repeat runs free, and re-attaching covers the element arriving late, being swapped for another,
  // and going away again. Nothing here sets state — the observer's own callback does, including for
  // the first delivery it makes when an element is newly observed — so a dependency-free effect
  // cannot start a chain of updates here.
  useEffect(() => {
    const element = ref.current;
    if (element === attached.current) return;
    attached.current = element;
    observer.current?.disconnect();
    observer.current = null;
    if (!element || typeof ResizeObserver === "undefined") return;

    const next = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (entry) setMeasured(entry.contentRect.width);
    });
    next.observe(element);
    observer.current = next;
  });

  // Unmount only. Clearing `attached` alongside matters under StrictMode, whose mount/unmount/
  // remount would otherwise leave the second mount observing nothing, because by then the element
  // looks unchanged to the attach step above.
  useEffect(
    () => () => {
      observer.current?.disconnect();
      observer.current = null;
      attached.current = null;
    },
    [],
  );

  return measured ?? fallback;
}
