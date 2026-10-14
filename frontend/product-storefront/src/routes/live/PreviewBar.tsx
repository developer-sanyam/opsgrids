import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { usePrefersReducedMotion } from "./usePrefersReducedMotion";

/** Ignore the pixel or two a rubber-band or a rounding produces. */
const SCROLL_THRESHOLD = 6;

/**
 * Is the window at rest somewhere the control cannot be in the way — the top of the page, or the
 * end of it, where `LiveStore` reserves the control's own height?
 */
function atSafeEdge(): boolean {
  const doc = document.documentElement;
  if (window.scrollY <= SCROLL_THRESHOLD) return true;
  return window.scrollY + window.innerHeight >= doc.scrollHeight - SCROLL_THRESHOLD;
}

/**
 * Show the control at the edges of the page, and while a buyer is scrolling *up*; take it out of
 * the way while they read down the page.
 *
 * A fixed control on a phone is one card-width from covering something, and the page is 2,700px
 * long. Reading down is exactly when nobody wants a preview chip in the corner; scrolling back up
 * is the gesture people make when they are looking for navigation, so that is when it returns.
 */
function useVisibleWhileWanted(): [boolean, (visible: boolean) => void] {
  const [visible, setVisible] = useState(true);
  const lastY = useRef(0);

  useEffect(() => {
    lastY.current = window.scrollY;
    let frame = 0;
    const onScroll = () => {
      if (frame) return;
      frame = window.requestAnimationFrame(() => {
        frame = 0;
        const y = window.scrollY;
        const delta = y - lastY.current;
        lastY.current = y;
        if (atSafeEdge()) setVisible(true);
        else if (delta > SCROLL_THRESHOLD) setVisible(false);
        else if (delta < -SCROLL_THRESHOLD) setVisible(true);
      });
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      window.removeEventListener("scroll", onScroll);
      if (frame) window.cancelAnimationFrame(frame);
    };
  }, []);

  return [visible, setVisible];
}

/**
 * The only thing on `/live` that is not the merchant's store: a small fixed control that says
 * where you are and gets you back.
 *
 * **It wears the console's skin on purpose.** Everything else on this route is drawn from the
 * merchant's tokens; this is drawn from the product's, so it can never read as part of the shop.
 * A buyer-looking "back to the editor" chip inside a store a merchant is about to screenshot would
 * be worse than no chip at all.
 *
 * It sits in the document *before* the store, so the first thing a keyboard or screen-reader user
 * meets is the way out rather than a store they have to tab through to leave. It is never hidden
 * from a keyboard: focus landing anywhere inside it brings it straight back.
 */
export function PreviewBar() {
  const [visible, setVisible] = useVisibleWhileWanted();
  const reducedMotion = usePrefersReducedMotion();

  return (
    <div
      // `sheet-safe-bottom` keeps the pill clear of the home indicator, which a bare `bottom-0` ignores.
      className="sheet-safe-bottom pointer-events-none fixed inset-x-0 bottom-0 z-50"
      style={{
        transform: visible ? "none" : "translateY(calc(100% + 1rem))",
        opacity: visible ? 1 : 0,
        // Motion is orientation here — the control leaves in the direction it will come back from.
        // Under `prefers-reduced-motion` it simply is or is not there.
        transition: reducedMotion ? "none" : "transform 200ms ease, opacity 200ms ease",
      }}
    >
      <div className="flex justify-start px-3 pb-3">
      <nav
        aria-label="Preview controls"
        className="rise pointer-events-auto flex items-center gap-1 rounded-full border border-line bg-surface p-1 shadow-float"
        onFocusCapture={() => setVisible(true)}
        onPointerEnter={() => setVisible(true)}
      >
        <span className="px-2 text-xs font-semibold uppercase tracking-wider text-muted">
          Preview
        </span>
        <Link
          to="/editor"
          className="inline-flex min-h-11 items-center rounded-full px-3 text-sm font-semibold text-ink transition-colors hover:bg-ground"
        >
          Editor
        </Link>
        <Link
          to="/"
          className="inline-flex min-h-11 items-center rounded-full px-3 text-sm font-semibold text-ink transition-colors hover:bg-ground"
        >
          Gallery
        </Link>
      </nav>
      </div>
    </div>
  );
}
