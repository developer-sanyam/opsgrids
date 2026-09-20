import { useEffect, useLayoutEffect } from "react";
import { useLocation, useNavigationType } from "react-router-dom";

/**
 * Scroll behaviour for a site that navigates without reloading.
 *
 * A real site puts you at the top of a page you just opened, and puts you back where you were when
 * you press Back. A single-page app does neither by default: the window keeps whatever scroll
 * offset the previous page left behind, so opening a product from halfway down the catalogue drops
 * the buyer into the middle of the product page.
 *
 * `<ScrollRestoration>` from the router would do most of this, but it requires a data router — it
 * throws under the `MemoryRouter` the component tests render in. Twenty lines here work in both,
 * and the restore is keyed on `location.key` so two visits to the same address keep their own
 * offsets.
 *
 * Restoring is deliberately instant rather than smooth, at every motion preference: an animated
 * scroll on a Back press is motion nobody asked for, and at `prefers-reduced-motion` it would be a
 * violation as well as an annoyance.
 */
const positions = new Map<string, number>();

function scrollWindowTo(top: number): void {
  if (typeof window.scrollTo !== "function") return;
  window.scrollTo({ top, left: 0, behavior: "instant" as ScrollBehavior });
}

export function useLiveScroll(): void {
  const location = useLocation();
  const navigationType = useNavigationType();

  // The browser's own restoration races ours and wins on a reload, landing the buyer mid-page on a
  // store that has not rendered yet. Taken over while live, handed back on the way out.
  useEffect(() => {
    const history = window.history;
    if (!("scrollRestoration" in history)) return;
    const previous = history.scrollRestoration;
    history.scrollRestoration = "manual";
    return () => {
      history.scrollRestoration = previous;
    };
  }, []);

  useLayoutEffect(() => {
    const key = location.key;
    const remembered = navigationType === "POP" ? positions.get(key) : undefined;
    const target = remembered ?? 0;
    scrollWindowTo(target);

    // Verified in Chrome, not assumed: at the moment this effect runs the document can still carry
    // the *previous* page's height, and the browser clamps a restore to it — pressing Back from a
    // shorter page landed 266px above where the buyer left. Re-applied once the new page has been
    // laid out, which is the frame after.
    let frame = 0;
    if (target > 0 && typeof window.requestAnimationFrame === "function") {
      frame = window.requestAnimationFrame(() => scrollWindowTo(target));
    }

    return () => {
      if (frame) window.cancelAnimationFrame(frame);
      positions.set(key, window.scrollY);
    };
    // `navigationType` is read at the moment the address changes and must not re-run this on its
    // own; the address is the event.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.key]);
}
