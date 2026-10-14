import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";

import { cn } from "../lib/cn";
import { ToastContext } from "../lib/toast";
import type { ToastControls, ToastOptions, ToastTone } from "../lib/toast";
import { AlertTriangleIcon, CheckCircleIcon, CircleDotIcon, CloseIcon, ServerIcon } from "../icons";

interface Toast {
  id: string;
  tone: ToastTone;
  title: string;
  description: string | undefined;
  reference: string | undefined;
  duration: number | null;
}

const DEFAULT_DURATION: Record<ToastTone, number> = {
  success: 5000,
  warning: 7000,
  error: 8000,
  info: 6000,
  system: 8000,
};

/** The word a screen reader hears in front of the title. Colour is never the only carrier. */
const TONE_WORD: Record<ToastTone, string> = {
  success: "Success:",
  warning: "Warning:",
  error: "Error:",
  info: "Note:",
  system: "System:",
};

/** Tone reaches the edge and the icon only; the words stay on ink, legible in both themes. */
const TONE_STYLE: Record<ToastTone, { edge: string; ink: string }> = {
  success: { edge: "border-l-positive", ink: "text-positive" },
  warning: { edge: "border-l-late", ink: "text-late" },
  error: { edge: "border-l-negative", ink: "text-negative" },
  info: { edge: "border-l-info", ink: "text-info" },
  system: { edge: "border-l-ink-soft", ink: "text-ink-soft" },
};

const TONE_ICON: Record<ToastTone, typeof CheckCircleIcon> = {
  success: CheckCircleIcon,
  warning: AlertTriangleIcon,
  error: AlertTriangleIcon,
  info: CircleDotIcon,
  system: ServerIcon,
};

/**
 * One toast, owning its own clock.
 *
 * The timer is suspended while the pointer is over it or focus is inside it, and the remaining
 * time is kept rather than restarted — someone who hovered to finish reading gets the rest of
 * what was left, not a fresh five seconds each time the mouse crosses it.
 */
function ToastItem({ toast, onDismiss }: { toast: Toast; onDismiss: (id: string) => void }) {
  const [paused, setPaused] = useState(false);
  const remaining = useRef(toast.duration);
  const startedAt = useRef(Date.now());
  const Icon = TONE_ICON[toast.tone];
  const style = TONE_STYLE[toast.tone];

  useEffect(() => {
    if (toast.duration === null) return;
    if (paused) {
      remaining.current = Math.max((remaining.current ?? 0) - (Date.now() - startedAt.current), 0);
      return;
    }
    startedAt.current = Date.now();
    const handle = setTimeout(() => onDismiss(toast.id), remaining.current ?? toast.duration);
    return () => {
      clearTimeout(handle);
    };
  }, [paused, toast.id, toast.duration, onDismiss]);

  return (
    <div
      className={cn(
        "rise pointer-events-auto flex w-full items-start gap-3 rounded-tile border-l-2 bg-surface",
        "px-4 py-3 shadow-float sm:w-96",
        style.edge,
      )}
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
      onFocusCapture={() => setPaused(true)}
      onBlurCapture={() => setPaused(false)}
    >
      <Icon className={cn("mt-0.5 size-4.5 shrink-0", style.ink)} aria-hidden />
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <p className="text-sm font-bold text-ink">{toast.title}</p>
        {toast.description ? (
          <p className="text-sm text-ink-soft">{toast.description}</p>
        ) : null}
        {toast.reference ? (
          <p className="tnum text-xs text-muted">Reference: {toast.reference}</p>
        ) : null}
      </div>
      <button
        type="button"
        onClick={() => onDismiss(toast.id)}
        aria-label={`Dismiss: ${toast.title}`}
        className="hit-44 relative grid size-7 shrink-0 place-items-center rounded-tile text-muted transition-colors hover:bg-sunken hover:text-ink"
      >
        <CloseIcon className="size-4" />
      </button>
    </div>
  );
}

/**
 * A live region that re-announces a repeated sentence.
 *
 * Assistive technology does not read text identical to what a region already holds, so "Widget
 * saved." twice in a row would be said once. Two regions alternating by sequence number is the
 * same trick `Announcer` uses, for the same reason.
 */
function LiveRegion({ message, seq, assertive }: { message: string; seq: number; assertive: boolean }) {
  const even = seq % 2 === 0;
  const live = assertive ? "assertive" : "polite";
  const role = assertive ? "alert" : "status";
  return (
    <div className="sr-only">
      <p aria-live={live} aria-atomic="true" role={role}>
        {even ? "" : message}
      </p>
      <p aria-live={live} aria-atomic="true" role={role}>
        {even ? message : ""}
      </p>
    </div>
  );
}

let counter = 0;

/**
 * The app's toast stack. Mount one at the root, above the router.
 *
 * Toasts outlive the thing that raised them — that is the whole point of the component. A create
 * dialog closes the instant the write lands, and the confirmation has to survive that; an inline
 * message inside the dialog would be unmounted before it was read.
 *
 * **The announcement is separate from the visible stack.** The stack carries no live role, and
 * the text is pushed into one of two permanent regions instead: polite for a success, assertive
 * for an error, because a failed write is the one thing worth interrupting for. Regions that
 * already exist announce reliably; a node that mounts carrying its own `role="status"` does not.
 *
 * Motion is `rise`, which `console.css` disables under `prefers-reduced-motion` — so a toast
 * appears rather than slides, and nothing here has to remember that.
 */
export function Toaster({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [polite, setPolite] = useState({ message: "", seq: 0 });
  const [assertive, setAssertive] = useState({ message: "", seq: 0 });

  const dismiss = useCallback((id: string) => {
    setToasts((previous) => previous.filter((toast) => toast.id !== id));
  }, []);

  const controls = useMemo<ToastControls>(() => {
    function push(tone: ToastTone, title: string, options: ToastOptions = {}): string {
      const id = `toast-${++counter}`;
      const toast: Toast = {
        id,
        tone,
        title,
        description: options.description,
        reference: options.reference,
        duration: options.duration === undefined ? DEFAULT_DURATION[tone] : options.duration,
      };
      setToasts((previous) => [...previous, toast]);
      const spoken = [TONE_WORD[tone], title, options.description].filter(Boolean).join(" ");
      const advance = (previous: { message: string; seq: number }) => ({
        message: spoken,
        seq: previous.seq + 1,
      });
      if (tone === "error") setAssertive(advance);
      else setPolite(advance);
      return id;
    }
    return {
      success: (title, options) => push("success", title, options),
      warning: (title, options) => push("warning", title, options),
      error: (title, options) => push("error", title, options),
      info: (title, options) => push("info", title, options),
      system: (title, options) => push("system", title, options),
      dismiss,
    };
  }, [dismiss]);

  return (
    <ToastContext value={controls}>
      {children}
      <LiveRegion message={polite.message} seq={polite.seq} assertive={false} />
      <LiveRegion message={assertive.message} seq={assertive.seq} assertive />
      {toasts.length > 0 ? (
        <div
          // `bottom-4`/`bottom-6` are the toast layer's own clearance; `--shell-bottom-inset`
          // (console.css) adds the fixed bottom navigation's height and safe area on top of it
          // when `AppShell` is rendering one — zero everywhere else, including desktop and
          // tablet, where there is no bottom nav to clear. The breakpoint matches `AppShell`'s
          // own (768px, `md`): below it there is a bottom nav to clear and the layer is
          // full-width; at and above it the layer anchors to the corner instead.
          style={{ bottom: "calc(1rem + var(--shell-bottom-inset))" }}
          className={cn(
            "toast-layer pointer-events-none fixed inset-x-4 flex flex-col-reverse gap-2",
            "md:inset-x-auto md:right-6 md:items-end",
          )}
        >
          {toasts.map((toast) => (
            <ToastItem key={toast.id} toast={toast} onDismiss={dismiss} />
          ))}
        </div>
      ) : null}
    </ToastContext>
  );
}
