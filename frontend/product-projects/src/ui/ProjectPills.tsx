import type {
  MilestoneStatus,
  ProjectHealth,
  ProjectPhase,
  TaskPriority,
} from "@/api/types";
import {
  HEALTH_LABEL,
  MILESTONE_STATUS_LABEL,
  PHASE_LABEL,
  PRIORITY_LABEL,
} from "@/data/labels";
import { StatusBadge, cn } from "@opsgrids/ui";
import type { StatusTone } from "@opsgrids/ui";
import { healthColor } from "./healthColor";

/* ------------------------------- health -------------------------------- */

/** "on_track", "at_risk" and "delayed" are all registered words. */
export function HealthPill({ health }: { health: ProjectHealth }) {
  return <StatusBadge status={health} label={HEALTH_LABEL[health]} />;
}

/** A bare health dot, for dense rows where a full pill would be too heavy. */
export function HealthDot({ health, className }: { health: ProjectHealth; className?: string }) {
  return (
    <span
      className={cn("inline-block size-2 rounded-full", className)}
      style={{ backgroundColor: healthColor(health) }}
      title={HEALTH_LABEL[health]}
      aria-hidden
    />
  );
}

/* -------------------------------- phase -------------------------------- */

/** A project phase — quiet neutral metadata, not a status, so it stays off the semantic palette. */
export function PhasePill({ phase }: { phase: ProjectPhase }) {
  const live = phase === "live";
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2.5 py-1 text-xs font-semibold",
        live ? "bg-accent-soft text-accent-ink" : "bg-ground text-ink-soft",
      )}
    >
      {PHASE_LABEL[phase]}
    </span>
  );
}

/* --------------------------- milestone status --------------------------- */

/** "completed", "on_track" and "due_soon"/"at_risk" are registered words; "slipping" is not. */
const MILESTONE_TONE: Record<MilestoneStatus, StatusTone> = {
  completed: "success",
  on_track: "success",
  due_soon: "warning",
  at_risk: "warning",
  slipping: "danger",
};

export function MilestoneStatusPill({ status }: { status: MilestoneStatus }) {
  return <StatusBadge tone={MILESTONE_TONE[status]} label={MILESTONE_STATUS_LABEL[status]} />;
}

/* ------------------------------- priority ------------------------------- */

/** "urgent", "high", "medium" and "low" are all registered words. */
export function PriorityPill({ priority }: { priority: TaskPriority }) {
  return (
    <StatusBadge status={priority} label={PRIORITY_LABEL[priority]} className="uppercase tracking-wide" />
  );
}

/* ------------------------------ progress ------------------------------- */

interface ProgressBarProps {
  /** 0–100. */
  value: number;
  /** Colour the fill by delivery health; defaults to the violet accent. */
  health?: ProjectHealth;
  className?: string;
  showLabel?: boolean;
}

/** A slim progress track. Fill colour follows health when given, else the accent. */
export function ProgressBar({ value, health, className, showLabel = false }: ProgressBarProps) {
  const color = health ? healthColor(health) : "var(--color-accent)";
  const clamped = Math.max(0, Math.min(100, value));
  return (
    <div className={cn("flex items-center gap-2", className)}>
      <div className="h-1.5 min-w-16 flex-1 overflow-hidden rounded-full bg-ground">
        <div
          className="h-full rounded-full"
          style={{ width: `${Math.max(clamped, 2)}%`, backgroundColor: color }}
        />
      </div>
      {showLabel ? (
        <span className="w-9 shrink-0 text-right text-xs font-semibold tnum text-ink-soft">
          {clamped}%
        </span>
      ) : null}
    </div>
  );
}
