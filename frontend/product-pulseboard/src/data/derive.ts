/**
 * Pure rollups over the fixture lists. No I/O, no React — just grouping, counting and
 * percentages, so the Overview's widgets all derive from the same project / task / timesheet
 * payloads. Kept pure so it is trivially testable.
 */
import type {
  AttentionItem,
  HealthBucket,
  Person,
  Project,
  ProjectStatus,
  Task,
  TimesheetRow,
} from "@/api/types";

/** Projects that are still running (anything but delivered). */
export function activeProjects(projects: readonly Project[]): number {
  return projects.filter((p) => p.status !== "done").length;
}

/** How many projects carry each health status. */
export function countByStatus(
  projects: readonly Project[],
  status: ProjectStatus,
): number {
  return projects.filter((p) => p.status === status).length;
}

const HEALTH_ORDER: { status: ProjectStatus; label: string }[] = [
  { status: "on_track", label: "On track" },
  { status: "at_risk", label: "At risk" },
  { status: "blocked", label: "Blocked" },
];

function pct(part: number, whole: number): number {
  return whole === 0 ? 0 : Math.round((part / whole) * 100);
}

/**
 * The project-health breakdown across the three live states (on track / at risk / blocked).
 * Percentages are of the active portfolio, so a delivered project doesn't dilute the picture.
 */
export function projectHealth(projects: readonly Project[]): HealthBucket[] {
  const active = activeProjects(projects);
  return HEALTH_ORDER.map(({ status, label }) => {
    const count = countByStatus(projects, status);
    return { status, label, count, pct: pct(count, active) };
  });
}

/** Open tasks (not done) whose due date falls within the given week. */
export function tasksDueThisWeek(
  tasks: readonly Task[],
  weekDates: readonly string[],
): number {
  const week = new Set(weekDates);
  return tasks.filter(
    (t) => t.status !== "done" && t.due_date !== null && week.has(t.due_date),
  ).length;
}

/** Total billable hours entered across the team this week. */
export function weeklyBillableHours(timesheets: readonly TimesheetRow[]): number {
  return timesheets.reduce(
    (sum, row) => sum + row.cells.reduce((s, c) => s + c.billable_hours, 0),
    0,
  );
}

/** Total hours (billable or not) entered across the team this week. */
export function weeklyEnteredHours(timesheets: readonly TimesheetRow[]): number {
  return timesheets.reduce(
    (sum, row) => sum + row.cells.reduce((s, c) => s + c.entered_hours, 0),
    0,
  );
}

/**
 * Team utilization for the week: billable hours as a share of total capacity. Returns a rounded
 * percentage; capacity comes from the roster so people at reduced capacity count fairly.
 */
export function teamUtilization(
  timesheets: readonly TimesheetRow[],
  people: readonly Person[],
): number {
  const capacity = people.reduce((s, p) => s + p.capacity_hours, 0);
  const billable = weeklyBillableHours(timesheets);
  return capacity === 0 ? 0 : Math.round((billable / capacity) * 1000) / 10;
}

const SEVERITY_RANK: Record<AttentionItem["severity"], number> = {
  blocked: 0,
  over_budget: 1,
  overdue: 2,
  at_risk: 3,
};

/**
 * Projects that need a second look, worst first. A project can trip several rules; the most
 * serious one wins and sets the reason: blocked > over budget > overdue > at risk. Delivered
 * projects never surface.
 */
export function needsAttention(
  projects: readonly Project[],
  today: string,
): AttentionItem[] {
  const items: AttentionItem[] = [];
  for (const p of projects) {
    if (p.status === "done") continue;

    let severity: AttentionItem["severity"] | null = null;
    let reason = "";

    if (p.status === "blocked") {
      severity = "blocked";
      reason = "Blocked — work has stalled and needs unblocking";
    } else if (p.spent_minor > p.budget_minor) {
      severity = "over_budget";
      const over = Math.round(((p.spent_minor - p.budget_minor) / p.budget_minor) * 100);
      reason = `Over budget by ${over}% — spend has passed the approved budget`;
    } else if (p.due_date < today) {
      severity = "overdue";
      reason = "Past its due date and still open";
    } else if (p.status === "at_risk") {
      severity = "at_risk";
      reason = "Flagged at risk — timeline or scope is under pressure";
    }

    if (severity !== null) {
      items.push({
        project_id: p.id,
        code: p.code,
        name: p.name,
        client: p.client,
        reason,
        severity,
      });
    }
  }
  return items.sort((a, b) => SEVERITY_RANK[a.severity] - SEVERITY_RANK[b.severity]);
}
