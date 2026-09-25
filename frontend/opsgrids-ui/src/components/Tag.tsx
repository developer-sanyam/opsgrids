import type { ReactNode } from "react";

import { cn } from "../lib/cn";

export type TagTone = "neutral" | "warning" | "positive" | "negative" | "info";

const TONES: Record<TagTone, string> = {
  neutral: "bg-neutral-soft text-neutral",
  warning: "bg-late-soft text-late",
  positive: "bg-present-soft text-present",
  negative: "bg-negative-soft text-negative",
  info: "bg-info-soft text-info",
};

/**
 * A quiet tag for metadata — an employment type, a warehouse, a plan interval, why a request is in
 * your inbox. Not a status: anything with a lifecycle meaning is a `StatusBadge`, so the same word
 * looks the same in every product.
 *
 * `tone` exists for the handful of tags that qualify rather than describe ("Unassigned — no
 * approver found"). It never carries the meaning on its own: the words do, and the tone agrees
 * with them.
 */
export function Tag({
  children,
  tone = "neutral",
  className,
}: {
  children: ReactNode;
  tone?: TagTone | undefined;
  className?: string | undefined;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-chip px-2 py-0.5 text-caption font-medium",
        TONES[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}
