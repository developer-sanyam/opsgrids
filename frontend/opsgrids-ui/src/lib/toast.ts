/**
 * The toast channel, kept apart from the component that mounts it so React Fast Refresh stays
 * happy — the same split as `announcer.ts` / `Announcer.tsx`.
 *
 * **Toasts and `useAnnounce` are different channels and must never carry the same sentence.**
 * `Announcer` is the screen's polite region for changes that have no visible home — "Loaded 25
 * more", "Showing 12 of 312". A toast *is* the visible home, and announces itself. Saying a
 * thing through both is how a screen reader user hears the same success twice.
 */

import { createContext, useContext } from "react";

export type ToastTone = "success" | "error" | "info";

export interface ToastOptions {
  /** A second line under the title. The detail; the title carries the fact. */
  description?: string;
  /**
   * Milliseconds before it dismisses itself, or `null` to stay until dismissed.
   *
   * Defaults: 5000 for a success, 8000 for an error — an error is usually longer to read and
   * more costly to miss. The clock pauses while the toast is hovered or holds focus.
   */
  duration?: number | null;
  /** `problem.request_id`, rendered small under the description. */
  reference?: string;
}

export interface ToastControls {
  /** "Widget created." Past tense, naming what happened — not "Success!". */
  success: (title: string, options?: ToastOptions) => string;
  /** The server's sentence, verbatim. Announced assertively. */
  error: (title: string, options?: ToastOptions) => string;
  /** Neither — "Export started, we'll email you when it's ready". */
  info: (title: string, options?: ToastOptions) => string;
  /** Dismiss one by the id the three above return. */
  dismiss: (id: string) => void;
}

export const ToastContext = createContext<ToastControls | null>(null);

const NOOP: ToastControls = {
  success: () => "",
  error: () => "",
  info: () => "",
  dismiss: () => undefined,
};

/**
 * Raise a toast.
 *
 * Outside a `<Toaster>` every method is a no-op rather than a throw, deliberately and for the
 * same reason `useAnnounce` is: a shared component telling the user its side effect landed must
 * not crash an app that has not mounted the provider yet. A missing `<Toaster>` costs the
 * feedback; a throw costs the screen. Mount one at the app root, above the router.
 */
export function useToast(): ToastControls {
  return useContext(ToastContext) ?? NOOP;
}
