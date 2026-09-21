/**
 * Typed PulseBoard API client.
 *
 * Live by default: every function calls the real backend through the gateway's same-origin
 * `/api/v1` prefix (Vite proxies it to `http://localhost:8100` in dev) with the bearer token
 * from the session. Set `VITE_USE_FIXTURES=true` to render the whole app from `fixtures.ts` with
 * no login — handy for offline design work.
 *
 * PulseBoard has no single `pulseboard` service — the gateway mounts three services with no
 * product segment, all gated by the `pulseboard` entitlement: `project` (`/projects`, the
 * portfolio, the board and the roster), `timesheet` (`/timesheets`, the weekly grid) and
 * `standup` (`/standups`, the daily check-ins). Every route returns the platform's paginated
 * envelope (`items` / `total`); the small `mapX` functions below turn one wire item into this
 * app's domain type, so a field rename on the wire is a one-line change here, not a hunt through
 * every screen.
 */

import { apiRequest } from "@opsgrids/client";
import { isoOf, mondayOf, weekFrom } from "./dates";
import {
  PROJECTS,
  STANDUPS,
  TASKS,
  TIMESHEETS,
  TODAY as FIXTURE_TODAY,
  WEEK_DATES as FIXTURE_WEEK_DATES,
  WEEK_START as FIXTURE_WEEK_START,
} from "./fixtures";
import { PEOPLE } from "@/data/people";
import type {
  Person,
  Priority,
  Project,
  ProjectStatus,
  StandupEntry,
  Task,
  TaskStatus,
  TimesheetRow,
  TimesheetStatus,
} from "./types";

/** Default source. Live unless `VITE_USE_FIXTURES=true` pins the app to fixtures.
 *  Dev builds only: `import.meta.env.DEV` compiles to `false` in production, so a stray
 *  CI/production env var can never ship fixture mode — an app rendering fabricated data
 *  with the "Sample data" markers suppressed. */
export const USE_FIXTURES = import.meta.env.DEV && import.meta.env.VITE_USE_FIXTURES === "true";

/** The fixture rows in `fixtures.ts` are generated from a pinned date, so the date itself is
 *  pinned too — move it and the standups, the timesheet week and the "due this week" rollups all
 *  drift out of sync with the rows built around it. Live traffic has no generated rows to stay
 *  consistent with, so it gets the real clock: pin that one too and every date-derived view on
 *  this screen would quietly keep serving the same stale week forever. */
const LIVE_TODAY = isoOf(new Date());

/** The current week's Monday and its seven ISO dates — shared by the Overview and Timesheets. */
export const TODAY = USE_FIXTURES ? FIXTURE_TODAY : LIVE_TODAY;
export const WEEK_START = USE_FIXTURES ? FIXTURE_WEEK_START : mondayOf(TODAY);
export const WEEK_DATES = USE_FIXTURES ? FIXTURE_WEEK_DATES : weekFrom(WEEK_START);

/** Small artificial delay so loading states are exercised in fixture mode. */
function settle<T>(value: T, ms = 240): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(value), ms));
}

/** A generous page size — one request returns the whole (small) working set for this org. The
 *  page params cap `size` at 200 (`platform_core.pagination`), so this is the largest single
 *  page the backend allows. */
const FULL_PAGE = 200;

/** The envelope every list endpoint on this platform returns. */
interface PageEnvelope<T> {
  items: T[];
  total: number;
}

/* ------------------------------------------------------------------------ wire shapes
 * One interface per service response, named for the Pydantic model that produces it
 * (`app/schemas.py` in `services/project`, `services/timesheet` and `services/standup`).
 * Kept private to this module: nothing outside the client layer should know the wire shape.
 */

interface PersonWire {
  id: string;
  name: string;
  role: string;
  location: string;
  capacity_hours: number;
}

interface ProjectWire {
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

interface TaskWire {
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

interface TimesheetCellWire {
  date: string;
  entered_hours: number;
  billable_hours: number;
}

interface TimesheetRowWire {
  person_id: string;
  week_start: string;
  status: TimesheetStatus;
  cells: TimesheetCellWire[];
}

interface StandupEntryWire {
  id: string;
  person_id: string;
  date: string;
  yesterday: string;
  today: string;
  blockers: string | null;
}

// Named aliases (rather than `apiRequest<PageEnvelope<X>>` at each call site) so every response
// type is a single identifier — one generic argument, not a nest of them.
type ProjectPage = PageEnvelope<ProjectWire>;
type TaskPage = PageEnvelope<TaskWire>;
type PersonPage = PageEnvelope<PersonWire>;
type TimesheetPage = PageEnvelope<TimesheetRowWire>;
type StandupPage = PageEnvelope<StandupEntryWire>;

/* ------------------------------------------------------------------------ mappers
 * Explicit, field-by-field — a wire field that is renamed or dropped fails typechecking here
 * instead of surfacing as `undefined` three components away.
 */

function mapPerson(raw: PersonWire): Person {
  return {
    id: raw.id,
    name: raw.name,
    role: raw.role,
    location: raw.location,
    capacity_hours: raw.capacity_hours,
  };
}

function mapProject(raw: ProjectWire): Project {
  return {
    id: raw.id,
    code: raw.code,
    name: raw.name,
    client: raw.client,
    status: raw.status,
    progress_pct: raw.progress_pct,
    budget_minor: raw.budget_minor,
    spent_minor: raw.spent_minor,
    currency: raw.currency,
    owner_id: raw.owner_id,
    start_date: raw.start_date,
    due_date: raw.due_date,
    billable: raw.billable,
    open_tasks: raw.open_tasks,
  };
}

function mapTask(raw: TaskWire): Task {
  return {
    id: raw.id,
    key: raw.key,
    title: raw.title,
    status: raw.status,
    assignee_id: raw.assignee_id,
    project_id: raw.project_id,
    estimate_hours: raw.estimate_hours,
    priority: raw.priority,
    tags: raw.tags,
    due_date: raw.due_date,
  };
}

function mapTimesheetRow(raw: TimesheetRowWire): TimesheetRow {
  return {
    person_id: raw.person_id,
    week_start: raw.week_start,
    status: raw.status,
    cells: raw.cells.map((c) => ({
      date: c.date,
      entered_hours: c.entered_hours,
      billable_hours: c.billable_hours,
    })),
  };
}

function mapStandupEntry(raw: StandupEntryWire): StandupEntry {
  return {
    id: raw.id,
    person_id: raw.person_id,
    date: raw.date,
    yesterday: raw.yesterday,
    today: raw.today,
    blockers: raw.blockers,
  };
}

export interface ListProjectsParams {
  search?: string;
  status?: ProjectStatus | "all";
}

/** The project list for the Projects screen, filtered by free-text search and health status. */
export async function listProjects(
  params: ListProjectsParams = {},
): Promise<Project[]> {
  if (USE_FIXTURES) {
    const q = params.search?.trim().toLowerCase() ?? "";
    const filtered = PROJECTS.filter((p) => {
      const matchesQuery =
        q === "" ||
        p.name.toLowerCase().includes(q) ||
        p.client.toLowerCase().includes(q) ||
        p.code.toLowerCase().includes(q);
      const matchesStatus =
        !params.status || params.status === "all" || p.status === params.status;
      return matchesQuery && matchesStatus;
    });
    return settle(filtered);
  }

  const query = new URLSearchParams();
  if (params.search?.trim()) query.set("q", params.search.trim());
  if (params.status && params.status !== "all") query.set("status", params.status);
  query.set("size", String(FULL_PAGE));
  const page = await apiRequest<ProjectPage>(`/projects?${query.toString()}`);
  return page.items.map(mapProject);
}

/** Every project (unfiltered) — powers the Overview rollups. */
export async function getProjects(): Promise<Project[]> {
  if (USE_FIXTURES) return settle(PROJECTS);
  const page = await apiRequest<ProjectPage>(`/projects?size=${FULL_PAGE}`);
  return page.items.map(mapProject);
}

/** Every task on the board. */
export async function getTasks(): Promise<Task[]> {
  if (USE_FIXTURES) return settle(TASKS);
  const page = await apiRequest<TaskPage>(`/projects/tasks?size=${FULL_PAGE}`);
  return page.items.map(mapTask);
}

/** The delivery-team roster. */
export async function getPeople(): Promise<Person[]> {
  if (USE_FIXTURES) return settle(PEOPLE);
  const page = await apiRequest<PersonPage>(`/projects/people?size=${FULL_PAGE}`);
  return page.items.map(mapPerson);
}

/** The current week's timesheets, one row per person. */
export async function getTimesheets(): Promise<TimesheetRow[]> {
  if (USE_FIXTURES) return settle(TIMESHEETS);
  const page = await apiRequest<TimesheetPage>(
    `/timesheets?week=${WEEK_START}&size=${FULL_PAGE}`,
  );
  return page.items.map(mapTimesheetRow);
}

/** Standup entries for a given day (defaults to today). */
export async function getStandups(date: string = TODAY): Promise<StandupEntry[]> {
  if (USE_FIXTURES) return settle(STANDUPS.filter((s) => s.date === date));
  const page = await apiRequest<StandupPage>(
    `/standups?date=${date}&size=${FULL_PAGE}`,
  );
  return page.items.map(mapStandupEntry);
}
