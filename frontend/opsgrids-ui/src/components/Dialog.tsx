import { useId, useRef } from "react";
import type { ReactNode, RefObject } from "react";
import { createPortal } from "react-dom";

import { cn } from "../lib/cn";
import { isTopDialogLayer } from "../lib/dialogStack";
import type { DialogLayer } from "../lib/dialogStack";
import { useFocusTrap } from "../lib/useFocusTrap";
import { CloseIcon } from "../icons";

interface DialogProps {
  open: boolean;
  /** Escape, the close control and — only when `dismissOnBackdrop` — the backdrop all call this. */
  onClose: () => void;
  title: string;
  /** The consequence, in a sentence. Tied to the dialog by `aria-describedby`. */
  description?: ReactNode | undefined;
  children?: ReactNode | undefined;
  /** The actions. Pinned to the bottom of the sheet on a phone, where the keyboard eats the view. */
  footer?: ReactNode | undefined;
  /**
   * Where focus lands on open. Defaults to the dialog's heading, which is right for a
   * confirmation; a form dialog points it at the first field it wants answered (the reject
   * dialog's comment, the correction dialog's first radio).
   */
  initialFocusRef?: RefObject<HTMLElement | null> | undefined;
  /**
   * Close on a backdrop click. Off by default: every dialog in this platform can hold typed text,
   * and losing a comment to a stray click outside is not a trade worth making. Escape still closes.
   */
  dismissOnBackdrop?: boolean | undefined;
  size?: "sm" | "md" | "lg" | undefined;
  className?: string | undefined;
}

const SIZES = { sm: "max-w-sm", md: "max-w-lg", lg: "max-w-2xl" };

/**
 * The modal dialog every confirmation in this platform uses.
 *
 * Focus moves in on open, is trapped while it is up, and returns to the control that opened it on
 * close — unless the caller navigated away, in which case the destination screen owns focus and
 * says what happened. Escape cancels. Below `sm` it is a full-height sheet with its actions
 * pinned, because a dialog whose confirm button sits under a phone keyboard is not operable.
 *
 * **Dialogs nest.** A dialog opened from inside one — "add a team member" from a create-deal form —
 * takes the keyboard for as long as it is up: Escape closes it and not the form beneath, Tab stays
 * inside it, and a click outside is measured against it. Closing it returns focus to the control
 * that opened it, inside the dialog underneath, which resumes its own trap. The depth is held in
 * `lib/dialogStack`, because document listeners fire in the order they were added and that is the
 * reverse of what the user sees.
 */
export function Dialog({
  open,
  onClose,
  title,
  description,
  children,
  footer,
  initialFocusRef,
  dismissOnBackdrop = false,
  size = "md",
  className,
}: DialogProps) {
  const panelRef = useRef<HTMLDivElement>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  /** This dialog's place in the stack while it is open. Read by the backdrop handler too. */
  const layerRef = useRef<DialogLayer | null>(null);
  const base = useId();
  const headingId = `${base}-title`;
  const descriptionId = description ? `${base}-description` : undefined;

  // The heading is where focus lands when the caller hasn't asked for a field — right for a
  // confirmation, where the consequence should be read before the actions.
  const focusTarget = initialFocusRef ?? headingRef;

  useFocusTrap({
    open,
    panelRef,
    onClose,
    initialFocusRef: focusTarget,
    lockBodyScroll: true,
    onLayerChange: (layer) => {
      layerRef.current = layer;
    },
  });

  if (!open) return null;

  /**
   * "Outside" belongs to the topmost dialog. While a dialog opened from inside this one is up,
   * this backdrop is beneath it and a click that reaches it is not the user dismissing this
   * dialog — it is a stray event, and closing on it would take the nested dialog down with it.
   */
  function onBackdropClick() {
    const layer = layerRef.current;
    if (layer !== null && !isTopDialogLayer(layer)) return;
    onClose();
  }

  /*
   * Portalled to `<body>`, and it has to be.
   *
   * `Dialog` positions itself with `position: fixed`, which is relative to the viewport only
   * while no ancestor is transformed, filtered, or otherwise given a containing block of its
   * own — and pages routinely are: `.rise`/`.cascade` (`console.css`) run a page-load animation
   * with `animation-fill-mode: both`, which holds the keyframe's final `transform` on the
   * element for good once the animation ends. A dialog mounted underneath that becomes trapped
   * inside it instead of the viewport — the backdrop stops at the transformed ancestor's edge
   * and the panel can be pushed off-screen top or bottom. Rendering through a portal means no
   * ancestor, present or future, can do that to it.
   */
  return createPortal(
    <div className="fixed inset-0 z-50 flex items-end justify-center sm:items-center sm:p-6">
      <div
        className="absolute inset-0 bg-scrim"
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
          "relative flex max-h-dvh w-full flex-col bg-surface shadow-overlay",
          "sheet-safe h-dvh rounded-none sm:h-auto sm:max-h-[85dvh] sm:rounded-card",
          SIZES[size],
          className,
        )}
      >
        <div className="flex items-start gap-3 p-5 sm:p-6 sm:pb-4">
          <div className="flex min-w-0 flex-1 flex-col gap-1">
            <h2
              id={headingId}
              ref={headingRef}
              tabIndex={-1}
              className="text-section text-balance focus:outline-none"
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
            aria-label="Close dialog"
            className="hit-44 relative grid size-9 shrink-0 place-items-center rounded-tile text-muted transition-colors hover:bg-sunken hover:text-ink"
          >
            <CloseIcon className="size-5" />
          </button>
        </div>

        {children ? (
          <div className="min-h-0 flex-1 overflow-y-auto px-5 pb-4 sm:px-6">{children}</div>
        ) : null}

        {footer ? (
          <div className="flex flex-row justify-end gap-2 border-t border-line p-4 sm:border-0 sm:p-6 sm:pt-4 flex-wrap max-sm:[&>*]:grow">
            {footer}
          </div>
        ) : null}
      </div>
    </div>,
    document.body,
  );
}
