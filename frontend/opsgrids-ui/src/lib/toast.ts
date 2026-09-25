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

/**
 * The five kinds of feedback the brief names (§12–14). Each has its own icon, edge colour and
 * spoken prefix, so the kind is never carried by colour alone.
 */
export type ToastTone = "success" | "warning" | "error" | "info" | "system";

export interface ToastOptions {
  /** A second line under the title. The detail; the title carries the fact. */
  description?: string | undefined;
  /**
   * Milliseconds before it dismisses itself, or `null` to stay until dismissed.
   *
   * Defaults: 5000 for a success, 6000 for info, 7000 for a warning, 8000 for an error or a
   * system notice — the costlier a message is to miss, the longer it stays. The clock pauses
   * while the toast is hovered or holds focus. A system notice that stays true until something
   * changes ("You're offline") passes `null` and is dismissed by the code that raised it.
   */
  duration?: number | null | undefined;
  /** `problem.request_id`, rendered small under the description. */
  reference?: string | undefined;
}

export interface ToastControls {
  /** "Widget created." Past tense, naming what happened — not "Success!". */
  success: (title: string, options?: ToastOptions) => string;
  /**
   * It worked, with a catch the user should act on: "Invoice sent. The customer has no email on
   * file for reminders." Announced politely — nothing failed.
   */
  warning: (title: string, options?: ToastOptions) => string;
  /** The server's sentence, verbatim. Announced assertively. */
  error: (title: string, options?: ToastOptions) => string;
  /** Neither — "Export started, we'll email you when it's ready". */
  info: (title: string, options?: ToastOptions) => string;
  /**
   * The platform, not the user's action: "You're offline.", "Scheduled maintenance tonight at
   * 23:00 IST.", "A new version is available." Announced politely.
   */
  system: (title: string, options?: ToastOptions) => string;
  /** Dismiss one by the id the three above return. */
  dismiss: (id: string) => void;
}

export const ToastContext = createContext<ToastControls | null>(null);

const NOOP: ToastControls = {
  success: () => "",
  warning: () => "",
  error: () => "",
  info: () => "",
  system: () => "",
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
