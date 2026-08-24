/**
 * Pure rollups over the live portfolio and milestone lists. No I/O, no React — just grouping,
 * counting and percentages, so the overview's widgets all derive from the same payloads the
 * portfolio and milestone screens read. Kept pure so it is trivially testable.
 */
import type {
  HealthSlice,
  MilestoneOut,
  MilestoneStatus,
  ProjectHealth,
  ProjectOut,
} from "@/api/types";
import { daysBetween } from "@/lib/format";
import { HEALTH_LABEL } from "./labels";

/** A project is "active" while it is still being delivered — anything short of the run phase. */
export function activeProjects(projects: readonly ProjectOut[]): number {
  return projects.filter((p) => p.phase !== "live").length;
}

const HEALTH_ORDER: ProjectHealth[] = ["on_track", "at_risk", "delayed"];

/** The delivery-health breakdown, in a stable order, with each health's share of the portfolio. */
export function healthBreakdown(projects: readonly ProjectOut[]): HealthSlice[] {
  const total = projects.length;
  return HEALTH_ORDER.map((key) => {
    const count = projects.filter((p) => p.health === key).length;
    return {
      key,
      label: HEALTH_LABEL[key],
      count,
      pct: total === 0 ? 0 : Math.round((count / total) * 100),
    };
  });
}

/** Milestone statuses that count as "on time" for the on-time rate. */
const ON_TIME: ReadonlySet<MilestoneStatus> = new Set<MilestoneStatus>([
  "completed",
  "on_track",
  "due_soon",
]);

/** Fraction (0–1) of milestones that are on time — completed or trending to hit their date. */
export function onTimeFraction(milestones: readonly MilestoneOut[]): number {
  if (milestones.length === 0) return 0;
  const onTime = milestones.filter((m) => ON_TIME.has(m.status)).length;
  return onTime / milestones.length;
}

/**
 * Milestones due within the next `withinDays` (including anything already overdue and unfinished).
 * Completed milestones never count.
 */
export function milestonesDue(
  milestones: readonly MilestoneOut[],
  now: string,
  withinDays = 14,
): number {
  return milestones.filter((m) => {
    if (m.status === "completed") return false;
    const days = daysBetween(now, m.due_date);
    return days <= withinDays;
  }).length;
}

const ATTENTION: ReadonlySet<MilestoneStatus> = new Set<MilestoneStatus>([
  "slipping",
  "at_risk",
]);

/** Milestones that need attention — slipping or at risk — soonest due first. */
export function needsAttention(milestones: readonly MilestoneOut[]): MilestoneOut[] {
  return milestones
    .filter((m) => ATTENTION.has(m.status))
    .sort((a, b) => a.due_date.localeCompare(b.due_date));
}

/** Total budget across the portfolio, in minor units. */
export function totalBudgetMinor(projects: readonly ProjectOut[]): number {
  return projects.reduce((sum, p) => sum + p.budget_minor, 0);
}

/** Total spend across the portfolio, in minor units. */
export function totalSpentMinor(projects: readonly ProjectOut[]): number {
  return projects.reduce((sum, p) => sum + p.spent_minor, 0);
}

/** Mean delivery progress across the given projects, rounded to a whole percent. */
export function averageProgress(projects: readonly ProjectOut[]): number {
  if (projects.length === 0) return 0;
  const sum = projects.reduce((acc, p) => acc + p.progress_pct, 0);
  return Math.round(sum / projects.length);
}
