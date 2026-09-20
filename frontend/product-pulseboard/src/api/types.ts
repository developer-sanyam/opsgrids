/**
 * Wire types for the PulseBoard product — projects, tasks, people, timesheets and standups.
 *
 * The PulseBoard service is a skeleton on this branch, so these shapes are modelled from the plan
 * and shaped like the responses the service will return: a generated client can drop in later
 * without the UI changing. Money is always an integer in minor units plus an ISO currency code
 * (ADR: no floats, no pre-rounded amounts reach the UI).
 */

export type ProjectStatus = "on_track" | "at_risk" | "blocked" | "done";
export type TaskStatus = "backlog" | "in_progress" | "review" | "done";
export type Priority = "low" | "medium" | "high";
export type TimesheetStatus = "draft" | "submitted" | "approved";

/** A member of the delivery team. Capacity is the billable-target hours for a standard week. */
export interface Person {
  id: string;
  name: string;
  role: string;
  location: string;
  capacity_hours: number;
}

/**
 * A client engagement. `progress_pct` is 0–100; `budget_minor` and `spent_minor` are integer
 * minor units of `currency`; `owner_id` references a `Person`. `owner_id`, `start_date` and
 * `due_date` are nullable — the project service stores them as optional columns, so an
 * engagement without an assigned owner or a set date is a real, representable state, not an
 * error.
 */
export interface Project {
  id: string;
  code: string;
  name: string;
  client: string;
  status: ProjectStatus;
  progress_pct: number;
  budget_minor: number;
  spent_minor: number;
  currency: string;
  owner_id: string | null;
  start_date: string | null;
  due_date: string | null;
  billable: boolean;
  open_tasks: number;
}

/** A card on the board. `estimate_hours` sizes it; `due_date` feeds the "due this week" rollup. */
export interface Task {
  id: string;
  key: string;
  title: string;
  status: TaskStatus;
  assignee_id: string | null;
  project_id: string;
  estimate_hours: number;
  priority: Priority;
  tags: string[];
  due_date: string | null;
}

/** One day's entry inside a person's weekly timesheet. `billable_hours <= entered_hours`. */
export interface TimesheetCell {
  date: string;
  entered_hours: number;
  billable_hours: number;
}

/** A person's timesheet for one week: seven daily cells and an approval state. */
export interface TimesheetRow {
  person_id: string;
  week_start: string;
  status: TimesheetStatus;
  cells: TimesheetCell[];
}

/** One person's standup for a given day: what they did, what's next, and anything blocking them. */
export interface StandupEntry {
  id: string;
  person_id: string;
  date: string;
  yesterday: string;
  today: string;
  blockers: string | null;
}

/** Small envelope every list endpoint returns. */
export interface Page<T> {
  items: T[];
  total: number;
}

/* --------------------------- overview rollups --------------------------- */

/** One bucket of the project-health breakdown — grouped live from the project list. */
export interface HealthBucket {
  status: ProjectStatus;
  label: string;
  count: number;
  pct: number;
}

/** A point on the utilization-over-time chart. */
export interface TrendPoint {
  label: string;
  value: number;
}

/** An item on the "needs attention" list, with the reason it surfaced. */
export interface AttentionItem {
  project_id: string;
  code: string;
  name: string;
  client: string;
  reason: string;
  severity: "at_risk" | "blocked" | "over_budget" | "overdue";
}
