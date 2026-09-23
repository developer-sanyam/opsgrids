import { useEffect, useRef } from "react";
import type { ReactNode } from "react";
import { createPortal } from "react-dom";

import { CloseIcon } from "./icons";

interface ModalProps {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: string;
  icon?: ReactNode;
  children: ReactNode;
}

/**
 * A centred modal dialog for operator action forms. Escape and a backdrop click close it, focus
 * moves into the dialog on open and the page behind it is inert to assistive tech (`aria-modal`).
 */
export function Modal({ open, onClose, title, description, icon, children }: ModalProps) {
  const panelRef = useRef<HTMLDivElement>(null);
  const headingId = useRef(`modal-${Math.random().toString(36).slice(2, 8)}`).current;

  useEffect(() => {
    if (!open) return;
    const previouslyFocused = document.activeElement as HTMLElement | null;
    panelRef.current?.focus();

    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    const { body } = document;
    const prevOverflow = body.style.overflow;
    body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      body.style.overflow = prevOverflow;
      previouslyFocused?.focus();
    };
  }, [open, onClose]);

  if (!open) return null;

  /*
   * Portalled to `<body>`. This is mounted from routes (`Plans.tsx`, `Grants.tsx`) that wrap
   * their content in `.rise`, whose page-load animation leaves a `transform` on the page
   * wrapper — which makes that wrapper the containing block for a `position: fixed` descendant
   * and traps the modal inside the page instead of the viewport. A portal has no such ancestor.
   */
  return createPortal(
    <div className="fixed inset-0 z-50 flex items-end justify-center p-3 sm:items-center sm:p-6">
      <div
        className="absolute inset-0 bg-ink/50 backdrop-blur-[2px]"
        onClick={onClose}
        aria-hidden
      />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={headingId}
        tabIndex={-1}
        className="relative w-full max-w-lg rounded-card bg-surface p-6 shadow-float focus:outline-none"
      >
        <div className="mb-4 flex items-start gap-3">
          {icon ? (
            <span className="grid size-10 shrink-0 place-items-center rounded-tile bg-accent-soft text-accent-ink">
              {icon}
            </span>
          ) : null}
          <div className="flex min-w-0 flex-1 flex-col gap-1">
            <h2 id={headingId} className="display text-xl font-extrabold">
              {title}
            </h2>
            {description ? <p className="text-sm text-muted">{description}</p> : null}
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close dialog"
            className="grid size-9 shrink-0 place-items-center rounded-full text-muted hover:bg-ground hover:text-ink transition-colors"
          >
            <CloseIcon className="size-5" />
          </button>
        </div>
        {children}
      </div>
    </div>,
    document.body,
  );
}
