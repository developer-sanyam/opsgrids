/**
 * Wire types for the Projects product — mirrors `services/projects/app/schemas.py` field for
 * field, so a schema rename fails typechecking here rather than surfacing as `undefined` three
 * components away. Money is carried as integer minor units (paise) with an ISO currency, never a
 * float.
 */

export type ProjectPhase =
  | "discovery"
  | "design"
  | "build"
  | "stabilize"
  | "launch"
  | "live";

/** Delivery health — the semantic traffic light, kept separate from the violet accent. */
export type ProjectHealth = "on_track" | "at_risk" | "delayed";

/** A milestone's lifecycle state. `slipping` = past due and unfinished; `at_risk` = trending late. */
export type MilestoneStatus =
  | "completed"
  | "on_track"
  | "due_soon"
  | "at_risk"
  | "slipping";

export type TaskStage = "backlog" | "in_progress" | "review" | "done";
export type TaskPriority = "low" | "medium" | "high" | "urgent";

/** A member of the delivery org, as embedded on a project owner / task assignee. */
export interface Person {
  id: string;
  name: string;
  role: string;
}

/** The full members-list row. `id` is the stable `ref` (`p-01`) every owner/assignee picker
 *  submits as `owner_ref` / `assignee_ref`. */
export interface MemberOut {
  id: string;
  name: string;
  role: string;
  directory_id: string | null;
}

/** Add a delivery-org member. `ref` is the stable handle — the same pattern as a project's `code`. */
export interface MemberIn {
  ref: string;
  name: string;
  role?: string;
  directory_id?: string | null;
}

/** Edit a member. Absent means "leave alone"; `ref` is immutable. */
export interface MemberUpdate {
  name?: string;
  role?: string;
  directory_id?: string | null;
}

/** A project in the portfolio. */
export interface ProjectOut {
  id: string;
  code: string;
  name: string;
  client: string;
  owner: Person;
  phase: ProjectPhase;
  health: ProjectHealth;
  /** Delivery progress, 0–100. */
  progress_pct: number;
  start_date: string;
  target_date: string;
  /** Budget and spend as integer minor units. */
  budget_minor: number;
  spent_minor: number;
  currency: string;
  team_size: number;
  open_tasks: number;
  milestone_count: number;
  summary: string;
}

/** Create a project. `owner_ref` names an existing member by their `ref`. */
export interface ProjectIn {
  code: string;
  name: string;
  client?: string;
  owner_ref?: string | null;
  phase?: ProjectPhase;
  health?: ProjectHealth;
  progress_pct?: number;
  start_date: string;
  target_date: string;
  budget_minor?: number;
  spent_minor?: number;
  currency?: string;
  team_size?: number;
  summary?: string;
}

/** Edit a project. Absent means "leave alone"; an explicit `null` on `owner_ref` clears the
 *  owner. `code` never travels here — it is the URL key. */
export interface ProjectUpdate {
  name?: string;
  client?: string;
  owner_ref?: string | null;
  phase?: ProjectPhase;
  health?: ProjectHealth;
  progress_pct?: number;
  start_date?: string;
  target_date?: string;
  budget_minor?: number;
  spent_minor?: number;
  currency?: string;
  team_size?: number;
  summary?: string;
}

/** A single milestone on a project. `due_date` is nullable: an undated milestone is legitimate. */
export interface MilestoneOut {
  id: string;
  project_id: string;
  project_code: string;
  project_name: string;
  title: string;
  owner: Person;
  due_date: string | null;
  status: MilestoneStatus;
  /** Completion of the milestone's checklist, 0–100. */
  progress_pct: number;
}

/** Create a milestone on a project (named by its `code`). */
export interface MilestoneIn {
  project_code: string;
  title: string;
  owner_ref?: string | null;
  due_date: string;
  status?: MilestoneStatus;
  progress_pct?: number;
}

/** Edit a milestone. Absent means "leave alone"; explicit `null` clears `owner_ref` / `due_date`.
 *  Not `project_code` — a milestone never changes project through this route. */
export interface MilestoneUpdate {
  title?: string;
  owner_ref?: string | null;
  due_date?: string | null;
  status?: MilestoneStatus;
  progress_pct?: number;
}

/** A card on the workflow board. */
export interface TaskOut {
  id: string;
  project_id: string;
  project_code: string;
  title: string;
  assignee: Person;
  stage: TaskStage;
  priority: TaskPriority;
  due_date: string | null;
  /** Estimate in story points. */
  points: number;
}

/** Create a board card on a project (named by its `code`). */
export interface TaskIn {
  project_code: string;
  title: string;
  assignee_ref?: string | null;
  stage?: TaskStage;
  priority?: TaskPriority;
  due_date?: string | null;
  points?: number;
}

/** Move or edit a board card. Absent means "leave alone"; explicit `null` clears `assignee_ref` /
 *  `due_date`. Not `project_code` — a card never changes project through this route. */
export interface TaskUpdate {
  title?: string;
  assignee_ref?: string | null;
  stage?: TaskStage;
  priority?: TaskPriority;
  due_date?: string | null;
  points?: number;
}

/** The platform's paginated envelope: `items` on the page, `total` matching the query overall. */
export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  size: number;
  pages: number;
}

/* --------------------------- dashboard rollups --------------------------- */

/** One slice of the delivery-health breakdown — grouped live from the portfolio. */
export interface HealthSlice {
  key: ProjectHealth;
  label: string;
  count: number;
  /** Share of the portfolio, 0–100. */
  pct: number;
}

/** A point on the release burndown: remaining scope, ideal guide vs actual. Representative only
 *  — `services/projects` has no reporting source behind this yet (`app/domain.py` constants). */
export interface BurndownPoint {
  label: string;
  ideal: number;
  actual: number | null;
}

export type BurndownRange = "sprint" | "release";
