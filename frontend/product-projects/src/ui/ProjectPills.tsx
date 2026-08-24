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
import { cn } from "@/lib/cn";
import { healthColor } from "./healthColor";

const PILL_BASE =
  "inline-flex items-center gap-1.5 rounded-full pl-2 pr-2.5 py-1 text-xs font-semibold";

/* ------------------------------- health -------------------------------- */

const HEALTH_TONE: Record<ProjectHealth, string> = {
  on_track: "bg-present-soft text-present",
  at_risk: "bg-late-soft text-late",
  delayed: "bg-danger-soft text-danger",
};

/** Delivery health, with a dot backing up the colour so meaning never rests on hue alone. */
export function HealthPill({ health }: { health: ProjectHealth }) {
  return (
    <span className={cn(PILL_BASE, HEALTH_TONE[health])}>
      <span className="size-1.5 rounded-full bg-current" aria-hidden />
      {HEALTH_LABEL[health]}
    </span>
  );
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

const MILESTONE_TONE: Record<MilestoneStatus, string> = {
  completed: "bg-present-soft text-present",
  on_track: "bg-info-soft text-info",
  due_soon: "bg-cyan-soft text-cyan",
  at_risk: "bg-late-soft text-late",
  slipping: "bg-danger-soft text-danger",
};

export function MilestoneStatusPill({ status }: { status: MilestoneStatus }) {
  return (
    <span className={cn(PILL_BASE, MILESTONE_TONE[status])}>
      <span className="size-1.5 rounded-full bg-current" aria-hidden />
      {MILESTONE_STATUS_LABEL[status]}
    </span>
  );
}

/* ------------------------------- priority ------------------------------- */

const PRIORITY_TONE: Record<TaskPriority, string> = {
  urgent: "bg-danger-soft text-danger",
  high: "bg-late-soft text-late",
  medium: "bg-info-soft text-info",
  low: "bg-ground text-muted",
};

export function PriorityPill({ priority }: { priority: TaskPriority }) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2 py-0.5 text-[0.7rem] font-bold uppercase tracking-wide",
        PRIORITY_TONE[priority],
      )}
    >
      {PRIORITY_LABEL[priority]}
    </span>
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
