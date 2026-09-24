import type { ReactNode } from "react";

import { cn } from "../lib/cn";
import { Button } from "./Button";

/** A shimmering placeholder block that reserves layout so nothing shifts on load. */
export function Skeleton({ className }: { className?: string | undefined }) {
  return (
    <div
      className={cn("animate-pulse rounded-full bg-sunken", className)}
      aria-hidden
    />
  );
}

interface EmptyStateProps {
  icon?: ReactNode | undefined;
  title: string;
  description: string;
  action?: ReactNode | undefined;
}

/** Every list gets one: says what happened and what to do next. */
export function EmptyState({ icon, title, description, action }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-12 text-center">
      {icon ? (
        <span className="grid size-12 place-items-center rounded-tile bg-sunken text-ink-soft">
          {icon}
        </span>
      ) : null}
      <div className="flex flex-col gap-1 max-w-sm">
        <p className="text-base font-bold">{title}</p>
        <p className="text-sm text-muted">{description}</p>
      </div>
      {action}
    </div>
  );
}

export type ErrorStateTone = "danger" | "warning" | "neutral";

const ERROR_TONE: Record<ErrorStateTone, string> = {
  danger: "text-negative",
  warning: "text-late",
  neutral: "text-ink",
};

interface ErrorStateProps {
  message: string;
  /**
   * The heading. Defaults to "Something went wrong", which is right for a 5xx and wrong for
   * everything else — a 403, a 404 and an offline state are different facts and say so
   * ("You can't do this", "That request isn't available to you", "You're offline").
   */
  title?: string | undefined;
  /**
   * `danger` for a failure, `warning` for a state that is not the caller's fault and not
   * broken, `neutral` for one that is simply unavailable. Colour never carries the meaning on
   * its own — the heading does.
   */
  tone?: ErrorStateTone | undefined;
  /**
   * The problem document's `request_id`, rendered as `Reference: 7f3c1a2e`. Support asks for it;
   * a user should never have to find it.
   */
  reference?: string | undefined;
  onRetry?: (() => void) | undefined;
  /** An action other than retry — "Back to approvals" on a 404, where retrying is pointless. */
  action?: ReactNode | undefined;
}

/**
 * A recoverable error, with what went wrong and a way out of it. `role="alert"` so it is
 * announced when it replaces a loading state without a click.
 */
export function ErrorState({
  message,
  title = "Something went wrong",
  tone = "danger",
  reference,
  onRetry,
  action,
}: ErrorStateProps) {
  return (
    <div
      className="flex flex-col items-center justify-center gap-3 py-12 text-center"
      role="alert"
    >
      <div className="flex flex-col gap-1 max-w-sm">
        <p className={cn("text-base font-bold", ERROR_TONE[tone])}>{title}</p>
        <p className="text-sm text-muted">{message}</p>
        {reference ? <p className="text-xs text-faint tnum">Reference: {reference}</p> : null}
      </div>
      {onRetry ? (
        // Secondary, not primary: retrying is the way out, not the reason the screen exists.
        <Button variant="secondary" size="sm" onClick={onRetry}>
          Try again
        </Button>
      ) : null}
      {action}
    </div>
  );
}

interface SectionHeaderProps {
  title: string;
  action?: ReactNode | undefined;
  subtitle?: string | undefined;
}

/** A card's internal title row: bold title left, optional control right. */
export function SectionHeader({ title, subtitle, action }: SectionHeaderProps) {
  return (
    <div className="flex items-start justify-between gap-3">
      <div className="flex flex-col gap-0.5">
        <h2 className="text-section">{title}</h2>
        {subtitle ? <p className="text-sm text-muted">{subtitle}</p> : null}
      </div>
      {action}
    </div>
  );
}
