import { useEffect, useRef } from "react";
import type { RefObject } from "react";

import { closeDialogLayer, isTopDialogLayer, openDialogLayer } from "./dialogStack";
import type { DialogLayer } from "./dialogStack";

const FOCUSABLE = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  '[tabindex]:not([tabindex="-1"])',
].join(", ");

export interface UseFocusTrapOptions {
  /** Whether the panel is open. The trap installs on the transition into `true` and tears down
   *  the moment it goes `false` — the same shape as `Dialog`. */
  open: boolean;
  /** The element Tab is trapped inside. */
  panelRef: RefObject<HTMLElement | null>;
  /** Escape, and the one call every closing control should route through — kept in a ref so the
   *  effect below can key on `open` alone and not re-run (and re-focus) on every parent render. */
  onClose: () => void;
  /** Where focus lands on open. Falls back to the panel itself if nothing else is given. */
  initialFocusRef?: RefObject<HTMLElement | null> | undefined;
  /** Locks page scroll for as long as the panel is open, the way a full-screen overlay should. */
  lockBodyScroll?: boolean | undefined;
  /**
   * Told the layer this instance was assigned, and told `null` again on teardown. `Dialog` uses
   * this to know whether *it* is still topmost when deciding if a backdrop click is its own —
   * the hook owns registration, but that one caller still needs to read it.
   */
  onLayerChange?: ((layer: DialogLayer | null) => void) | undefined;
}

/**
 * The keyboard contract every modal overlay in this platform shares: focus moves in on open, Tab
 * stays inside, Escape closes, and focus returns to whatever opened it on close.
 *
 * Registers a layer in `dialogStack` so nested overlays behave the same way `Dialog` already does
 * — only the topmost one answers Escape or traps Tab. A lone overlay (the mobile nav drawer, which
 * never nests) is simply always topmost, so the same code path is correct for it too.
 *
 * `Dialog.tsx` and the mobile nav drawer in `AppShell.tsx` both call this rather than each keeping
 * its own copy (ADR-0001 — no forking a component's behaviour into an app, and no forking it
 * between components in the library either).
 */
export function useFocusTrap({
  open,
  panelRef,
  onClose,
  initialFocusRef,
  lockBodyScroll = false,
  onLayerChange,
}: UseFocusTrapOptions): void {
  const onCloseRef = useRef(onClose);
  useEffect(() => {
    onCloseRef.current = onClose;
  });

  useEffect(() => {
    if (!open) return;
    const layer = openDialogLayer();
    onLayerChange?.(layer);
    const previouslyFocused = document.activeElement as HTMLElement | null;
    // One frame later: the panel is mounted and the caller's target ref is populated.
    const target = initialFocusRef?.current ?? panelRef.current;
    target?.focus();

    function onKeyDown(event: KeyboardEvent) {
      // An overlay opened from inside this one owns Escape and the trap while it is up.
      if (!isTopDialogLayer(layer)) return;
      if (event.key === "Escape") {
        event.stopPropagation();
        onCloseRef.current();
        return;
      }
      if (event.key !== "Tab") return;
      const panel = panelRef.current;
      if (!panel) return;
      const focusable = Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
        (element) => element.offsetParent !== null || element === document.activeElement,
      );
      if (focusable.length === 0) {
        event.preventDefault();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (!first || !last) return;
      const active = document.activeElement;
      // `indexOf` is -1 for the initial-focus target when it isn't itself tab-reachable (a
      // heading with `tabIndex={-1}`, say) — treat that the same as "sitting on the first
      // control" so shift+Tab from the landing spot still wraps to the end.
      const activeIndex = active instanceof HTMLElement ? focusable.indexOf(active) : -1;
      if (event.shiftKey && activeIndex <= 0) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && activeIndex === focusable.length - 1) {
        event.preventDefault();
        first.focus();
      }
    }

    document.addEventListener("keydown", onKeyDown, true);
    const { body } = document;
    const previousOverflow = body.style.overflow;
    if (lockBodyScroll) body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKeyDown, true);
      // Off the stack before focus moves: an overlay beneath must already be topmost when it
      // receives the focus that comes back to its own control.
      closeDialogLayer(layer);
      onLayerChange?.(null);
      if (lockBodyScroll) body.style.overflow = previousOverflow;
      previouslyFocused?.focus();
    };
    // `initialFocusRef` is a ref object callers hold in `useRef`, stable across renders; `onClose`
    // and `onLayerChange` are re-read fresh each open via closures captured here, which is fine —
    // callers pass stable identities for both. Keying on `open` alone is intentional.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);
}
