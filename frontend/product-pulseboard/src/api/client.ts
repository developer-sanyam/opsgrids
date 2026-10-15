/**
 * Typed PulseBoard API client.
 *
 * Live by default: every function calls the real backend through the gateway's same-origin
 * `/api/v1` prefix (Vite proxies it to `http://localhost:8100` in dev) with the bearer token
 * from the session. Set `VITE_USE_FIXTURES=true` to render the whole app from `fixtures.ts` with
 * no login — handy for offline design work. Dev builds only: `import.meta.env.DEV` compiles to
 * `false` in production, so a stray CI/production env var can never ship fixture mode.
 *
 * PulseBoard has no single `pulseboard` service — the gateway mounts three services with no
 * product segment, all gated by the `pulseboard` entitlement: `project` (`/projects`, the
 * portfolio, the board and the roster), `timesheet` (`/timesheets`, the weekly grid) and
 * `standup` (`/standups`, the daily check-ins). Every list route returns the platform's
 * paginated envelope (`items` / `total` / `page` / `size` / `pages`); the small `mapX` functions
 * below turn one wire item into this app's domain type, so a field rename on the wire is a
 * one-line change here, not a hunt through every screen.
 */

import { apiRequest, buildQuery, dateInZone, settle } from "@opsgrids/client";
import { mondayOf, weekFrom } from "./dates";
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
  Page,
  Person,
  PersonIn,
  PersonPatch,
  Priority,
  Project,
  ProjectIn,
  ProjectStatus,
  ProjectUpdate,
  StandupEntry,
  StandupEntryIn,
  Task,
  TaskIn,
  TaskStatus,
  TaskUpdate,
  TimesheetEntry,
  TimesheetRow,
  TimesheetStatus,
  TimesheetStatusIn,
  TimesheetSummary,
  TimesheetWeekIn,
  TrendPoint,
} from "./types";

/** Default source. Live unless `VITE_USE_FIXTURES=true` pins the app to fixtures. */
export const USE_FIXTURES = import.meta.env.DEV && import.meta.env.VITE_USE_FIXTURES === "true";

/** The fixture rows in `fixtures.ts` are generated from a pinned date, so the date itself is
 *  pinned too — move it and the standups, the timesheet week and the "due this week" rollups all
 *  drift out of sync with the rows built around it. Live traffic gets the real clock. */
const LIVE_TODAY = dateInZone(new Date()); // browser-local, not UTC; module load is before sign-in

/** The current week's Monday and its seven ISO dates — shared by the Overview and Timesheets. */
export const TODAY = USE_FIXTURES ? FIXTURE_TODAY : LIVE_TODAY;
export const WEEK_START = USE_FIXTURES ? FIXTURE_WEEK_START : mondayOf(TODAY);
export const WEEK_DATES = USE_FIXTURES ? FIXTURE_WEEK_DATES : weekFrom(WEEK_START);

/** The default page size for a screen with real server paging. */
export const DEFAULT_PAGE_SIZE = 25;

/**
 * The largest page the backend allows (`platform_core.pagination` caps `size` at 200). Used only
 * by aggregate widgets that need the whole working set to add up a number — never by a list
 * screen, which pages through `{items,total,page,size,pages}` instead of capping silently.
 */
const ROLLUP_PAGE = 200;

/** PulseBoard keeps an empty filter (`?q=`) on the wire, as it always has. */
function qs(params: Record<string, string | number | boolean | undefined>): string {
  return buildQuery(params, { keepEmpty: true });
}

/* ------------------------------------------------------------------------ wire shapes
 * One interface per service response, named for the Pydantic model that produces it
 * (`app/schemas.py` in `services/project`, `services/timesheet` and `services/standup`).
 * Kept private to this module: nothing outside the client layer should know the wire shape.
 */

interface PersonWire {
  id: string;
  uuid: string;
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

interface TimesheetEntryWire {
  date: string;
  project_ref: string | null;
  entered_hours: number;
  billable_hours: number;
}

interface TimesheetRowWire {
  person_id: string;
  week_start: string;
  status: TimesheetStatus;
  cells: TimesheetCellWire[];
  entries: TimesheetEntryWire[];
}

interface StandupEntryWire {
  id: string;
  person_id: string;
  date: string;
  yesterday: string;
  today: string;
  blockers: string | null;
}

interface PageWire<T> {
  items: T[];
  total: number;
  page: number;
  size: number;
  pages: number;
}

interface TimesheetSummaryWire {
  week_start: string;
  entered_hours: number;
  billable_hours: number;
  capacity_hours: number;
  utilization_pct: number;
  /** `null` for a week with no timesheets at all — a real "no data", not a 0% week. */
  series: { label: string; value: number | null }[];
}

/* ------------------------------------------------------------------------ mappers
 * Explicit, field-by-field — a wire field that is renamed or dropped fails typechecking here
 * instead of surfacing as `undefined` three components away.
 */

function mapPerson(raw: PersonWire): Person {
  return {
    id: raw.id,
    uuid: raw.uuid,
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

function mapTimesheetEntry(raw: TimesheetEntryWire): TimesheetEntry {
  return {
    date: raw.date,
    project_ref: raw.project_ref,
    entered_hours: raw.entered_hours,
    billable_hours: raw.billable_hours,
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
    entries: raw.entries.map(mapTimesheetEntry),
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

function mapPage<W, T>(raw: PageWire<W>, mapItem: (w: W) => T): Page<T> {
  return {
    items: raw.items.map(mapItem),
    total: raw.total,
    page: raw.page,
    size: raw.size,
    pages: raw.pages,
  };
}

/** A fixture-mode page envelope over an already-filtered, already-sorted array. */
function pageOf<T>(all: T[], page: number, size: number): Page<T> {
  const start = (page - 1) * size;
  return {
    items: all.slice(start, start + size),
    total: all.length,
    page,
    size,
    pages: Math.max(1, Math.ceil(all.length / size)),
  };
}

/* ------------------------------------------------------------------------ projects */

export interface ListProjectsParams {
  page?: number;
  size?: number;
  q?: string | undefined;
  status?: ProjectStatus | "all" | undefined;
  billable?: boolean | undefined;
  sort?: "code" | "progress" | "due" | undefined;
}

/** The project list for the Projects screen: search, health filter, billing filter and sort. */
export async function listProjects(params: ListProjectsParams = {}): Promise<Page<Project>> {
  const page = params.page ?? 1;
  const size = params.size ?? DEFAULT_PAGE_SIZE;
  if (USE_FIXTURES) {
    const q = params.q?.trim().toLowerCase() ?? "";
    let filtered = PROJECTS.filter((p) => {
      const matchesQuery =
        q === "" ||
        p.name.toLowerCase().includes(q) ||
        p.client.toLowerCase().includes(q) ||
        p.code.toLowerCase().includes(q);
      const matchesStatus = !params.status || params.status === "all" || p.status === params.status;
      const matchesBillable = params.billable === undefined || p.billable === params.billable;
      return matchesQuery && matchesStatus && matchesBillable;
    });
    if (params.sort === "code") filtered = [...filtered].sort((a, b) => a.code.localeCompare(b.code));
    if (params.sort === "progress") filtered = [...filtered].sort((a, b) => b.progress_pct - a.progress_pct);
    if (params.sort === "due")
      filtered = [...filtered].sort((a, b) => (a.due_date ?? "9999").localeCompare(b.due_date ?? "9999"));
    return settle(pageOf(filtered, page, size));
  }

  const query = qs({
    q: params.q?.trim() || undefined,
    status: params.status && params.status !== "all" ? params.status : undefined,
    billable: params.billable,
    sort: params.sort,
    page,
    size,
  });
  const raw = await apiRequest<PageWire<ProjectWire>>(`/projects${query}`);
  return mapPage(raw, mapProject);
}

/** Every engagement, one full page — used to resolve codes against ids in a join (the board,
 * task and timesheet forms' project pickers). */
export async function getProjects(): Promise<Project[]> {
  const result = await listProjects({ page: 1, size: ROLLUP_PAGE });
  return result.items;
}

export async function createProject(input: ProjectIn): Promise<Project> {
  if (USE_FIXTURES) {
    const { owner_ref, ...rest } = input;
    const created: Project = { id: `PB-${input.code}`, owner_id: owner_ref, ...rest };
    PROJECTS.push(created);
    return settle(created);
  }
  const raw = await apiRequest<ProjectWire>("/projects", { method: "POST", body: input });
  return mapProject(raw);
}

export async function updateProject(code: string, input: ProjectUpdate): Promise<Project> {
  if (USE_FIXTURES) {
    const found = PROJECTS.find((p) => p.code === code);
    if (!found) throw new Error("Not found");
    Object.assign(found, input);
    return settle(found);
  }
  const raw = await apiRequest<ProjectWire>(`/projects/${encodeURIComponent(code)}`, {
    method: "PATCH",
    body: input,
  });
  return mapProject(raw);
}

export async function deleteProject(code: string): Promise<void> {
  if (USE_FIXTURES) {
    const index = PROJECTS.findIndex((p) => p.code === code);
    if (index >= 0) PROJECTS.splice(index, 1);
    return settle(undefined);
  }
  await apiRequest<void>(`/projects/${encodeURIComponent(code)}`, { method: "DELETE" });
}

/* ------------------------------------------------------------------------ tasks (the board) */

export interface ListTasksParams {
  page?: number;
  size?: number;
  status?: TaskStatus | undefined;
  project?: string | undefined;
  assignee?: string | undefined;
}

export async function listTasks(params: ListTasksParams = {}): Promise<Page<Task>> {
  const page = params.page ?? 1;
  const size = params.size ?? ROLLUP_PAGE;
  if (USE_FIXTURES) {
    const filtered = TASKS.filter(
      (t) =>
        (!params.status || t.status === params.status) &&
        (!params.project || t.project_id === params.project) &&
        (!params.assignee || t.assignee_id === params.assignee),
    );
    return settle(pageOf(filtered, page, size));
  }
  const query = qs({ status: params.status, project: params.project, assignee: params.assignee, page, size });
  const raw = await apiRequest<PageWire<TaskWire>>(`/projects/tasks${query}`);
  return mapPage(raw, mapTask);
}

/** Every task on the board, one full page — the Overview's rollups need the whole set. */
export async function getTasks(): Promise<Task[]> {
  const result = await listTasks({ page: 1, size: ROLLUP_PAGE });
  return result.items;
}

export async function createTask(input: TaskIn): Promise<Task> {
  if (USE_FIXTURES) {
    const project = PROJECTS.find((p) => p.code === input.project_code);
    const created: Task = {
      id: `t-${input.key}`,
      key: input.key,
      title: input.title,
      status: input.status,
      assignee_id: input.assignee_ref,
      project_id: project?.id ?? input.project_code,
      estimate_hours: input.estimate_hours,
      priority: input.priority,
      tags: input.tags,
      due_date: input.due_date,
    };
    TASKS.push(created);
    return settle(created);
  }
  const raw = await apiRequest<TaskWire>("/projects/tasks", { method: "POST", body: input });
  return mapTask(raw);
}

export async function updateTask(key: string, input: TaskUpdate): Promise<Task> {
  if (USE_FIXTURES) {
    const found = TASKS.find((t) => t.key === key);
    if (!found) throw new Error("Not found");
    if (input.title !== undefined) found.title = input.title;
    if (input.status !== undefined) found.status = input.status;
    if ("assignee_ref" in input) found.assignee_id = input.assignee_ref ?? null;
    if (input.estimate_hours !== undefined) found.estimate_hours = input.estimate_hours;
    if (input.priority !== undefined) found.priority = input.priority;
    if (input.tags !== undefined) found.tags = input.tags;
    if ("due_date" in input) found.due_date = input.due_date ?? null;
    return settle(found);
  }
  const raw = await apiRequest<TaskWire>(`/projects/tasks/${encodeURIComponent(key)}`, {
    method: "PATCH",
    body: input,
  });
  return mapTask(raw);
}

export async function deleteTask(key: string): Promise<void> {
  if (USE_FIXTURES) {
    const index = TASKS.findIndex((t) => t.key === key);
    if (index >= 0) TASKS.splice(index, 1);
    return settle(undefined);
  }
  await apiRequest<void>(`/projects/tasks/${encodeURIComponent(key)}`, { method: "DELETE" });
}

/* ------------------------------------------------------------------------ people (the roster) */

export interface ListPeopleParams {
  page?: number;
  size?: number;
}

export async function listPeople(params: ListPeopleParams = {}): Promise<Page<Person>> {
  const page = params.page ?? 1;
  const size = params.size ?? DEFAULT_PAGE_SIZE;
  if (USE_FIXTURES) return settle(pageOf(PEOPLE, page, size));
  const raw = await apiRequest<PageWire<PersonWire>>(`/projects/people${qs({ page, size })}`);
  return mapPage(raw, mapPerson);
}

/** Every person on the roster, one full page — used to resolve names against ids in a join. */
export async function getPeople(): Promise<Person[]> {
  const result = await listPeople({ page: 1, size: ROLLUP_PAGE });
  return result.items;
}

export async function createPerson(input: PersonIn): Promise<Person> {
  if (USE_FIXTURES) {
    const created: Person = { id: input.ref, uuid: `uuid-${input.ref}`, ...input };
    PEOPLE.push(created);
    return settle(created);
  }
  const raw = await apiRequest<PersonWire>("/projects/people", { method: "POST", body: input });
  return mapPerson(raw);
}

export async function updatePerson(ref: string, input: PersonPatch): Promise<Person> {
  if (USE_FIXTURES) {
    const found = PEOPLE.find((p) => p.id === ref);
    if (!found) throw new Error("Not found");
    Object.assign(found, input);
    return settle(found);
  }
  const raw = await apiRequest<PersonWire>(`/projects/people/${encodeURIComponent(ref)}`, {
    method: "PATCH",
    body: input,
  });
  return mapPerson(raw);
}

export async function deletePerson(ref: string): Promise<void> {
  if (USE_FIXTURES) {
    const index = PEOPLE.findIndex((p) => p.id === ref);
    if (index >= 0) PEOPLE.splice(index, 1);
    return settle(undefined);
  }
  await apiRequest<void>(`/projects/people/${encodeURIComponent(ref)}`, { method: "DELETE" });
}

/* ------------------------------------------------------------------------ timesheets */

export interface ListTimesheetsParams {
  page?: number;
  size?: number;
  week?: string | undefined;
  status?: TimesheetStatus | undefined;
}

export async function listTimesheets(params: ListTimesheetsParams = {}): Promise<Page<TimesheetRow>> {
  const page = params.page ?? 1;
  const size = params.size ?? ROLLUP_PAGE;
  const week = params.week ?? WEEK_START;
  if (USE_FIXTURES) {
    const filtered = TIMESHEETS.filter(
      (r) => r.week_start === week && (!params.status || r.status === params.status),
    );
    return settle(pageOf(filtered, page, size));
  }
  const query = qs({ week, status: params.status, page, size });
  const raw = await apiRequest<PageWire<TimesheetRowWire>>(`/timesheets${query}`);
  return mapPage(raw, mapTimesheetRow);
}

/** The current week's timesheets, one full page — the Overview's rollups need the whole set. */
export async function getTimesheets(): Promise<TimesheetRow[]> {
  const result = await listTimesheets({ page: 1, size: ROLLUP_PAGE });
  return result.items;
}

/** Record (or re-record) one person's week. Resend every entry — a partial PUT wipes the rest. */
export async function recordTimesheetWeek(input: TimesheetWeekIn): Promise<TimesheetRow> {
  if (USE_FIXTURES) {
    const existing = TIMESHEETS.find(
      (r) => r.person_id === input.person_ref && r.week_start === input.week_start,
    );
    const row: TimesheetRow = {
      person_id: input.person_ref,
      week_start: input.week_start,
      status: input.status,
      cells: WEEK_DATES.map((date) => {
        const entry = input.entries.find((e) => e.date === date);
        return { date, entered_hours: entry?.entered_hours ?? 0, billable_hours: entry?.billable_hours ?? 0 };
      }),
      entries: input.entries,
    };
    if (existing) Object.assign(existing, row);
    else TIMESHEETS.push(row);
    return settle(row);
  }
  const raw = await apiRequest<TimesheetRowWire>("/timesheets", { method: "PUT", body: input });
  return mapTimesheetRow(raw);
}

/** Submit, approve or reopen one person's week. Entries are untouched — only `status` moves. */
export async function setTimesheetStatus(input: TimesheetStatusIn): Promise<TimesheetRow> {
  if (USE_FIXTURES) {
    const found = TIMESHEETS.find(
      (r) => r.person_id === input.person_ref && r.week_start === input.week_start,
    );
    if (!found) throw new Error("Not found");
    found.status = input.status;
    return settle(found);
  }
  const raw = await apiRequest<TimesheetRowWire>("/timesheets/status", { method: "POST", body: input });
  return mapTimesheetRow(raw);
}

/** The Overview's and Timesheets' time-side rollup for one week — real server arithmetic. */
export async function getTimesheetSummary(week: string = WEEK_START): Promise<TimesheetSummary> {
  if (USE_FIXTURES) {
    const rows = TIMESHEETS.filter((r) => r.week_start === week);
    const entered = rows.reduce((s, r) => s + r.cells.reduce((cs, c) => cs + c.entered_hours, 0), 0);
    const billable = rows.reduce((s, r) => s + r.cells.reduce((cs, c) => cs + c.billable_hours, 0), 0);
    const capacity = PEOPLE.reduce((s, p) => s + p.capacity_hours, 0);
    const utilization_pct = capacity === 0 ? 0 : Math.round((billable / capacity) * 1000) / 10;
    return settle({
      week_start: week,
      entered_hours: entered,
      billable_hours: billable,
      capacity_hours: capacity,
      utilization_pct,
      series: [{ label: "This week", value: utilization_pct }],
    });
  }
  const wire = await apiRequest<TimesheetSummaryWire>(`/timesheets/summary${qs({ week })}`);
  // Weeks with no timesheets are skipped rather than plotted as 0%. The headline week is always
  // real, so an org with no history still gets a one-point series, as fixture mode does.
  const series = wire.series.filter((p): p is TrendPoint => p.value !== null);
  return {
    ...wire,
    series: series.length > 0 ? series : [{ label: "This week", value: wire.utilization_pct }],
  };
}

/* ------------------------------------------------------------------------ standups */

export interface ListStandupsParams {
  page?: number;
  size?: number;
  date?: string | undefined;
  blocked?: boolean | undefined;
}

export async function listStandups(params: ListStandupsParams = {}): Promise<Page<StandupEntry>> {
  const page = params.page ?? 1;
  const size = params.size ?? ROLLUP_PAGE;
  const date = params.date ?? TODAY;
  if (USE_FIXTURES) {
    const filtered = STANDUPS.filter(
      (s) => s.date === date && (!params.blocked || s.blockers !== null),
    );
    return settle(pageOf(filtered, page, size));
  }
  const query = qs({ date, blocked: params.blocked, page, size });
  const raw = await apiRequest<PageWire<StandupEntryWire>>(`/standups${query}`);
  return mapPage(raw, mapStandupEntry);
}

/** Post (or repost) a day's check-in. Reposting the same person+day replaces the entry. */
export async function postStandup(input: StandupEntryIn): Promise<StandupEntry> {
  if (USE_FIXTURES) {
    const existing = STANDUPS.find((s) => s.person_id === input.person_ref && s.date === input.date);
    const entry: StandupEntry = {
      id: existing?.id ?? `s-${input.person_ref}-${input.date}`,
      person_id: input.person_ref,
      date: input.date,
      yesterday: input.yesterday,
      today: input.today,
      blockers: input.blockers,
    };
    if (existing) Object.assign(existing, entry);
    else STANDUPS.push(entry);
    return settle(entry);
  }
  const raw = await apiRequest<StandupEntryWire>("/standups", { method: "POST", body: input });
  return mapStandupEntry(raw);
}
