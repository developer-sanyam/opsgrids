import type { ReactNode } from "react";

import { cn } from "../lib/cn";
import { AlertTriangleIcon, CheckCircleIcon, CircleDotIcon } from "../icons";

export type InlineAlertTone = "info" | "warning" | "danger" | "success";

/**
 * How loudly this is said.
 *
 * `auto` announces a danger immediately and everything else politely — right for an alert that
 * *appears* in response to something. A state that is simply present on first load passes `off`:
 * a banner that announces itself on every visit is noise a user learns to talk over.
 */
export type InlineAlertLive = "auto" | "off";

const TONES: Record<InlineAlertTone, { fill: string; edge: string; ink: string }> = {
  info: { fill: "bg-info-soft", edge: "border-info", ink: "text-info" },
  warning: { fill: "bg-late-soft", edge: "border-late", ink: "text-late" },
  danger: { fill: "bg-negative-soft", edge: "border-negative", ink: "text-negative" },
  success: { fill: "bg-positive-soft", edge: "border-positive", ink: "text-positive" },
};

function iconFor(tone: InlineAlertTone) {
  if (tone === "success") return CheckCircleIcon;
  if (tone === "info") return CircleDotIcon;
  return AlertTriangleIcon;
}

interface InlineAlertProps {
  tone?: InlineAlertTone | undefined;
  /** The fact, in a sentence. This carries the meaning — the colour never does. */
  title: string;
  children?: ReactNode | undefined;
  /** One control at most: "Show overdue", "Try again". */
  action?: ReactNode | undefined;
  /** `problem.request_id`, rendered as `Reference: 7f3c1a2e`. */
  reference?: string | undefined;
  live?: InlineAlertLive | undefined;
  className?: string | undefined;
}

/**
 * An inline message that qualifies what is around it: "Approved, but the correction couldn't be
 * applied", "You already have a delegation that covers those dates", "{n} requests are overdue".
 *
 * Tone reaches the icon and the leading edge, never the body text — a soft tint under a tinted
 * sentence is where inline alerts fail contrast. The heading and the body stay on ink, which is
 * legible in both themes and readable to anyone who does not see the tint at all.
 */
export function InlineAlert({
  tone = "info",
  title,
  children,
  action,
  reference,
  live = "auto",
  className,
}: InlineAlertProps) {
  const styles = TONES[tone];
  const Icon = iconFor(tone);
  const role = live === "off" ? undefined : tone === "danger" ? "alert" : "status";

  return (
    <div
      role={role}
      className={cn(
        // `border-l-2`, not an arbitrary width: the same leading-edge treatment Timeline's
        // quoted blockquote uses, off Tailwind's built-in border-width scale rather than a
        // one-off value.
        "flex flex-wrap items-start gap-3 rounded-tile border-l-2 px-4 py-3",
        styles.fill,
        styles.edge,
        className,
      )}
    >
      <Icon className={cn("mt-0.5 size-4.5 shrink-0", styles.ink)} aria-hidden />
      <div className="flex min-w-0 flex-1 basis-48 flex-col gap-1">
        <p className="text-sm font-bold text-ink">{title}</p>
        {children ? <div className="text-sm text-ink-soft">{children}</div> : null}
        {reference ? <p className="text-xs text-muted tnum">Reference: {reference}</p> : null}
      </div>
      {action ? <div className="shrink-0">{action}</div> : null}
    </div>
  );
}
