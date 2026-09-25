import { useCallback, useSyncExternalStore } from "react";

/**
 * The brief's tablet boundary (§2): below this a table becomes cards and a filter row becomes a
 * sheet. Tailwind's `md`, named once so the JS switch and the CSS classes cannot disagree.
 */
export const TABLET_QUERY = "(min-width: 768px)";

/**
 * Whether a media query matches, kept current as the viewport changes.
 *
 * For the few places that must render *different markup* per width — a table versus a list of
 * cards — rather than restyle the same markup, which CSS does better. Rendering both and hiding
 * one with CSS would put every row's controls in the document twice: two "Edit Acme Ltd" buttons
 * for a test (or a screen reader in a browser without the stylesheet) to trip over.
 *
 * `fallback` is the answer where `matchMedia` does not exist — jsdom, which every app's unit
 * tests run in. Callers pass the answer that keeps today's markup, so no existing test sees a
 * different tree.
 */
export function useMediaQuery(query: string, fallback: boolean): boolean {
  const subscribe = useCallback(
    (onChange: () => void) => {
      if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
        return () => undefined;
      }
      const list = window.matchMedia(query);
      list.addEventListener("change", onChange);
      return () => list.removeEventListener("change", onChange);
    },
    [query],
  );
  const read = () =>
    typeof window === "undefined" || typeof window.matchMedia !== "function"
      ? fallback
      : window.matchMedia(query).matches;
  return useSyncExternalStore(subscribe, read, () => fallback);
}
