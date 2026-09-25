import { useId, useRef } from "react";
import type { ReactNode, RefObject } from "react";
import { createPortal } from "react-dom";

import { CloseIcon } from "../icons";
import { cn } from "../lib/cn";
import { isTopDialogLayer } from "../lib/dialogStack";
import type { DialogLayer } from "../lib/dialogStack";
import { useFocusTrap } from "../lib/useFocusTrap";

interface DrawerProps {
  open: boolean;
  /** Escape, the close control and — only when `dismissOnBackdrop` — the backdrop all call this. */
  onClose: () => void;
  title: string;
  /** A sentence of context under the title, tied by `aria-describedby`. */
  description?: ReactNode | undefined;
  children?: ReactNode | undefined;
  /** The actions, pinned under the scrolling body so they stay in reach. */
  footer?: ReactNode | undefined;
  /** The edge it slides from at 768px and up. Below that it is always a bottom sheet. */
  side?: "right" | "left" | undefined;
  /** The panel's width at 768px and up: `sm` 384px, `md` 448px (default), `lg` 672px. */
  size?: "sm" | "md" | "lg" | undefined;
  /** Where focus lands on open. Defaults to the heading. */
  initialFocusRef?: RefObject<HTMLElement | null> | undefined;
  /**
   * Close on a click or tap outside. Off by default for the same reason as `Dialog`: a drawer can
   * hold typed text. A read-only sheet — filters that apply as they change, a record preview —
   * turns it on, because that is how people expect to dismiss a bottom sheet.
   */
  dismissOnBackdrop?: boolean | undefined;
}

const WIDTH = { sm: "md:max-w-sm", md: "md:max-w-md", lg: "md:max-w-2xl" };

/**
 * A panel that slides over the page: from the side at tablet width and up, from the bottom on a
 * phone. For detail beside a list, a filter set, a secondary form — anything that should not take
 * the user off the screen they are on.
 *
 * It is a modal dialog in every way that matters, and shares `Dialog`'s machinery rather than
 * copying it: focus moves in on open, Tab is trapped, Escape closes, focus returns to what opened
 * it, the page behind does not scroll, and it nests in the dialog stack (a `ConfirmDialog` opened
 * from inside it owns Escape until it closes). Portalled to `<body>` for the reason `Dialog`
 * explains: a transformed ancestor would otherwise trap its fixed positioning.
 *
 * Motion is one transform, from off-screen to rest, and `console.css` collapses every transition
 * under `prefers-reduced-motion` — it appears rather than slides, with nothing here to remember.
 */
export function Drawer({
  open,
  onClose,
  title,
  description,
  children,
  footer,
  side = "right",
  size = "md",
  initialFocusRef,
  dismissOnBackdrop = false,
}: DrawerProps) {
  const panelRef = useRef<HTMLDivElement>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const layerRef = useRef<DialogLayer | null>(null);
  const base = useId();
  const headingId = `${base}-title`;
  const descriptionId = description ? `${base}-description` : undefined;

  useFocusTrap({
    open,
    panelRef,
    onClose,
    initialFocusRef: initialFocusRef ?? headingRef,
    lockBodyScroll: true,
    onLayerChange: (layer) => {
      layerRef.current = layer;
    },
  });

  if (!open) return null;

  function onBackdropClick() {
    const layer = layerRef.current;
    if (layer !== null && !isTopDialogLayer(layer)) return;
    onClose();
  }

  return createPortal(
    <div className="fixed inset-0 z-50">
      <div
        className="absolute inset-0 bg-ink/50"
        onClick={dismissOnBackdrop ? onBackdropClick : undefined}
        aria-hidden
      />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={headingId}
        aria-describedby={descriptionId}
        className={cn(
          // Phone: a sheet from the bottom, never taller than most of the screen, so the page it
          // came from stays visible above it.
          "absolute inset-x-0 bottom-0 flex max-h-11/12 flex-col rounded-t-card bg-surface shadow-float",
          "transition-transform duration-200 ease-out starting:translate-y-full",
          // Tablet and up: full height against one edge.
          "md:inset-y-0 md:max-h-none md:w-full md:rounded-none",
          "md:starting:translate-y-0",
          side === "right"
            ? "md:left-auto md:right-0 md:starting:translate-x-full"
            : "md:left-0 md:right-auto md:starting:-translate-x-full",
          WIDTH[size],
        )}
      >
        <div className="flex items-start gap-3 border-b border-line p-4 md:p-6">
          <div className="flex min-w-0 flex-1 flex-col gap-1">
            <h2
              id={headingId}
              ref={headingRef}
              tabIndex={-1}
              // The heading is where focus lands so a screen reader starts at the title; it is not
              // a control, so it draws no ring. Inline because console.css's global
              // `:focus-visible` rule is unlayered and outranks any utility (the ring `Dialog`'s
              // heading still shows — VISUAL-TESTING.md).
              style={{ outline: "none" }}
              className="display text-lg font-bold"
            >
              {title}
            </h2>
            {description ? (
              <div id={descriptionId} className="text-sm text-muted">
                {description}
              </div>
            ) : null}
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label={`Close ${title}`}
            className="grid size-9 shrink-0 place-items-center rounded-full text-muted transition-colors hover:bg-ground hover:text-ink"
          >
            <CloseIcon className="size-5" />
          </button>
        </div>

        {children ? (
          <div
            // A stop of its own so a keyboard can scroll a body with nothing focusable in it (axe's
            // scrollable-region-focusable, which `Dialog`'s long content still fails).
            // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- a scroll container must be keyboard-reachable (WCAG 2.1.1); it has no role to add.
            tabIndex={0}
            className="min-h-0 flex-1 overflow-y-auto p-4 focus-visible:-outline-offset-2 md:p-6"
          >
            {children}
          </div>
        ) : null}

        {footer ? (
          <div className="flex flex-col-reverse gap-2 border-t border-line p-4 sm:flex-row sm:justify-end md:px-6">
            {footer}
          </div>
        ) : null}
      </div>
    </div>,
    document.body,
  );
}
