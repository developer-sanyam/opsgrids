import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

/** A shimmering placeholder block that reserves layout so nothing shifts on load. */
export function Skeleton({ className }: { className?: string }) {
  return (
    <div
      className={cn("animate-pulse rounded-full bg-ground", className)}
      aria-hidden
    />
  );
}

interface EmptyStateProps {
  icon?: ReactNode;
  title: string;
  description: string;
  action?: ReactNode;
}

/** Every list gets one: says what happened and what to do next. */
export function EmptyState({ icon, title, description, action }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-12 text-center">
      {icon ? (
        <span className="grid size-14 place-items-center rounded-full bg-ground text-ink-soft">
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

/** A recoverable error, with what went wrong and a way to retry. */
export function ErrorState({
  message,
  onRetry,
}: {
  message: string;
  onRetry?: () => void;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-12 text-center">
      <div className="flex flex-col gap-1 max-w-sm">
        <p className="text-base font-bold text-danger">Something went wrong</p>
        <p className="text-sm text-muted">{message}</p>
      </div>
      {onRetry ? (
        <button
          type="button"
          onClick={onRetry}
          className="rounded-full bg-ink px-4 h-9 text-sm font-semibold text-white hover:bg-ink-soft transition-colors"
        >
          Try again
        </button>
      ) : null}
    </div>
  );
}

interface SectionHeaderProps {
  title: string;
  action?: ReactNode;
  subtitle?: string;
}

/** A card's internal title row: bold title left, optional control right. */
export function SectionHeader({ title, subtitle, action }: SectionHeaderProps) {
  return (
    <div className="flex items-start justify-between gap-3">
      <div className="flex flex-col gap-0.5">
        <h2 className="text-lg font-bold tracking-tight">{title}</h2>
        {subtitle ? <p className="text-sm text-muted">{subtitle}</p> : null}
      </div>
      {action}
    </div>
  );
}
