/**
 * Wire types for the Projects product.
 *
 * These mirror the shapes the `projects` service will return once its backend graduates from a
 * skeleton — a portfolio of projects, their milestones, and a task board — so a generated client
 * can drop in later without the UI changing. Money is carried as integer minor units (paise) with
 * an ISO currency, never a float.
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

/** A member of the delivery org — a project owner or a task assignee. */
export interface Person {
  id: string;
  name: string;
  role: string;
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
  /** Budget and spend as integer minor units (paise). */
  budget_minor: number;
  spent_minor: number;
  currency: string;
  team_size: number;
  open_tasks: number;
  milestone_count: number;
  summary: string;
}

/** A single milestone on a project. */
export interface MilestoneOut {
  id: string;
  project_id: string;
  project_code: string;
  project_name: string;
  title: string;
  owner: Person;
  due_date: string;
  status: MilestoneStatus;
  /** Completion of the milestone's checklist, 0–100. */
  progress_pct: number;
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

/** Small envelope every list endpoint returns. */
export interface Page<T> {
  items: T[];
  total: number;
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

/** A point on the release burndown: remaining scope, ideal guide vs actual. */
export interface BurndownPoint {
  label: string;
  ideal: number;
  actual: number | null;
}

export type BurndownRange = "sprint" | "release";
