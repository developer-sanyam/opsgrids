/**
 * Wire types for the PulseBoard product — projects, tasks, people, timesheets and standups.
 *
 * These shapes mirror the real services (`services/project`, `services/timesheet`,
 * `services/standup`) field for field. Money is always an integer in minor units plus an ISO
 * currency code (ADR: no floats, no pre-rounded amounts reach the UI).
 */

export type ProjectStatus = "on_track" | "at_risk" | "blocked" | "done";
export type TaskStatus = "backlog" | "in_progress" | "review" | "done";
export type Priority = "low" | "medium" | "high";
export type TimesheetStatus = "draft" | "submitted" | "approved";

/**
 * A member of the delivery team. `id` is the stable `ref` (`p01`) every `owner_id` /
 * `assignee_id` / timesheet `person_id` on the wire joins against. `uuid` is the roster person's
 * opaque id — the value the timesheet and standup *write* endpoints require as `person_id`,
 * since nothing else on the wire carries it.
 */
export interface Person {
  id: string;
  uuid: string;
  name: string;
  role: string;
  location: string;
  capacity_hours: number;
}

/** Add a roster person. `ref` is the stable handle — immutable once created. */
export interface PersonIn {
  ref: string;
  name: string;
  role: string;
  location: string;
  capacity_hours: number;
}

/** Edit a roster person. Absent means "leave alone"; `ref` is never sent. */
export interface PersonPatch {
  name?: string;
  role?: string;
  location?: string;
  capacity_hours?: number;
}

/**
 * A client engagement. `progress_pct` is 0–100; `budget_minor` and `spent_minor` are integer
 * minor units of `currency`; `owner_id` references a `Person` by `ref`. `owner_id`, `start_date`
 * and `due_date` are nullable — an engagement without an assigned owner or a set date is a real,
 * representable state, not an error.
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

/** Create an engagement. `owner_ref` names an existing person by their `ref`. */
export interface ProjectIn {
  code: string;
  name: string;
  client: string;
  status: ProjectStatus;
  progress_pct: number;
  budget_minor: number;
  spent_minor: number;
  currency: string;
  owner_ref: string | null;
  start_date: string | null;
  due_date: string | null;
  billable: boolean;
  open_tasks: number;
}

/** Edit an engagement. Absent means "leave alone". `code` is never sent — it is immutable. */
export interface ProjectUpdate {
  name?: string;
  client?: string;
  status?: ProjectStatus;
  progress_pct?: number;
  budget_minor?: number;
  spent_minor?: number;
  currency?: string;
  owner_ref?: string | null;
  start_date?: string | null;
  due_date?: string | null;
  billable?: boolean;
  open_tasks?: number;
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

/** Create a board card on an engagement, named by its `code`. */
export interface TaskIn {
  key: string;
  project_code: string;
  title: string;
  status: TaskStatus;
  assignee_ref: string | null;
  estimate_hours: number;
  priority: Priority;
  tags: string[];
  due_date: string | null;
}

/**
 * Move or edit a board card. Absent means "leave alone"; an explicit `null` on `assignee_ref` or
 * `due_date` clears it — `JSON.stringify` drops an `undefined` property but keeps a `null` one,
 * so those two are the way this object tells the server which is meant.
 */
export interface TaskUpdate {
  title?: string;
  status?: TaskStatus;
  assignee_ref?: string | null;
  estimate_hours?: number;
  priority?: Priority;
  tags?: string[];
  due_date?: string | null;
}

/** One recorded slot of work inside a person's week: a date, an engagement and decimal hours. */
export interface TimesheetEntry {
  date: string;
  project_ref: string | null;
  entered_hours: number;
  billable_hours: number;
}

/** One day's entry inside a person's weekly timesheet. `billable_hours <= entered_hours`. */
export interface TimesheetCell {
  date: string;
  entered_hours: number;
  billable_hours: number;
}

/**
 * A person's timesheet for one week: seven daily cells, the per-slot detail those cells are
 * summed from, and an approval state.
 */
export interface TimesheetRow {
  person_id: string;
  week_start: string;
  status: TimesheetStatus;
  cells: TimesheetCell[];
  entries: TimesheetEntry[];
}

/** One slot of work inside a submitted week. */
export interface TimesheetEntryIn {
  date: string;
  project_ref: string | null;
  entered_hours: number;
  billable_hours: number;
}

/** Record (or re-record) one person's week. Re-recording replaces every entry on the sheet.
 *  Always a draft: a submitted or approved week is a 409 until reopened via `/timesheets/status`. */
export interface TimesheetWeekIn {
  person_ref: string;
  person_id: string;
  week_start: string;
  status: "draft";
  capacity_hours: number;
  entries: TimesheetEntryIn[];
}

/** Submit, approve or reopen one person's week. Only `status` moves — entries are untouched. */
export interface TimesheetStatusIn {
  person_ref: string;
  week_start: string;
  status: TimesheetStatus;
}

/** A point on the utilization-over-time chart. */
export interface TrendPoint {
  label: string;
  value: number;
}

/** The Overview's time-side rollup for one week. */
export interface TimesheetSummary {
  week_start: string;
  entered_hours: number;
  billable_hours: number;
  capacity_hours: number;
  utilization_pct: number;
  series: TrendPoint[];
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

/** Post (or repost) a day's check-in. Reposting the same person+day replaces the entry. */
export interface StandupEntryIn {
  person_ref: string;
  person_id: string;
  date: string;
  yesterday: string;
  today: string;
  blockers: string | null;
}

/** The envelope every list endpoint on this platform returns. */
export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  size: number;
  pages: number;
}

/* --------------------------- overview rollups --------------------------- */

/** One bucket of the project-health breakdown — grouped live from the project list. */
export interface HealthBucket {
  status: ProjectStatus;
  label: string;
  count: number;
  pct: number;
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
