/**
 * Typed Projects API client.
 *
 * Live by default: every function calls the projects service through the gateway at the
 * same-origin `/api/v1/projects-workspace/...` prefix (Vite proxies it to `http://localhost:8100`
 * in dev) with the bearer token from the session. Set `VITE_USE_FIXTURES=true` in a dev build to
 * render the whole app from `fixtures.ts` with no login — handy for offline design work; a stray
 * production env var can never enable it, since `import.meta.env.DEV` compiles to `false` there.
 *
 * `services/projects/app/schemas.py` mirrors this app's `types.ts` field for field, so every
 * response is typed straight off the wire with no mapping layer — a renamed field fails
 * typechecking here, not three components away.
 */

import { apiRequest, settle } from "@opsgrids/client";
import { MEMBERS, MILESTONES, PROJECTS, TASKS } from "./fixtures";
import type {
  MemberIn,
  MemberOut,
  MemberUpdate,
  MilestoneIn,
  MilestoneOut,
  MilestoneStatus,
  MilestoneUpdate,
  Page,
  ProjectHealth,
  ProjectIn,
  ProjectOut,
  ProjectPhase,
  ProjectUpdate,
  TaskIn,
  TaskOut,
  TaskStage,
  TaskUpdate,
} from "./types";

/** Default source. Live unless `VITE_USE_FIXTURES=true` pins the app to fixtures.
 *  Dev builds only: `import.meta.env.DEV` compiles to `false` in production, so a stray
 *  CI/production env var can never ship fixture mode — an app rendering fabricated data
 *  with the "Sample data" markers suppressed. */
export const USE_FIXTURES = import.meta.env.DEV && import.meta.env.VITE_USE_FIXTURES === "true";

/** True when the app is talking to the real gateway. */
export const LIVE = !USE_FIXTURES;

/** The default page size for a screen with real server paging (Portfolio, Team). */
export const DEFAULT_PAGE_SIZE = 25;

/**
 * The largest page the backend allows (`platform_core.pagination` caps `size` at 200). Used only
 * by the "whole portfolio" loaders (Overview, Timeline, Board, Milestones) that need every row to
 * add a total or fill every stage — never by a paged list screen, which pages through
 * `{items,total,page,size,pages}` instead of assuming everything fits on one request.
 */
const ROLLUP_PAGE = 200;

function qs(params: Record<string, string | number | boolean | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === "") continue;
    search.set(key, String(value));
  }
  const s = search.toString();
  return s ? `?${s}` : "";
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

/**
 * Fetch every page of a paginated loader and flatten it — the "whole set" a rollup or a board
 * needs, without assuming everything fits under the backend's per-page cap.
 */
async function fetchAll<T>(
  loader: (page: number, size: number) => Promise<Page<T>>,
): Promise<T[]> {
  const first = await loader(1, ROLLUP_PAGE);
  const items = [...first.items];
  for (let page = 2; page <= first.pages; page += 1) {
    const next = await loader(page, ROLLUP_PAGE);
    items.push(...next.items);
  }
  return items;
}

/* ------------------------------------------------------------------------ projects */

export interface ListProjectsParams {
  page?: number;
  size?: number;
  q?: string;
  health?: ProjectHealth | "all";
  phase?: ProjectPhase | "all";
  sort?: "code" | "progress" | "target" | "name" | "health";
}

const HEALTH_RANK: Record<ProjectHealth, number> = { delayed: 0, at_risk: 1, on_track: 2 };

/** The project list for the Portfolio screen: search, health/phase filters, sort and real paging. */
export async function listProjects(params: ListProjectsParams = {}): Promise<Page<ProjectOut>> {
  const page = params.page ?? 1;
  const size = params.size ?? DEFAULT_PAGE_SIZE;
  if (USE_FIXTURES) {
    const q = params.q?.trim().toLowerCase() ?? "";
    let filtered = PROJECTS.filter((p) => {
      const matchesQuery =
        q === "" ||
        p.name.toLowerCase().includes(q) ||
        p.code.toLowerCase().includes(q) ||
        p.client.toLowerCase().includes(q) ||
        p.summary.toLowerCase().includes(q) ||
        p.owner.name.toLowerCase().includes(q);
      const matchesHealth =
        !params.health || params.health === "all" || p.health === params.health;
      const matchesPhase = !params.phase || params.phase === "all" || p.phase === params.phase;
      return matchesQuery && matchesHealth && matchesPhase;
    });
    filtered = [...filtered].sort((a, b) => {
      switch (params.sort) {
        case "progress":
          return b.progress_pct - a.progress_pct;
        case "target":
          return a.target_date.localeCompare(b.target_date);
        case "name":
          return a.name.localeCompare(b.name);
        case "health":
          return HEALTH_RANK[a.health] - HEALTH_RANK[b.health];
        case "code":
        default:
          return a.code.localeCompare(b.code);
      }
    });
    return settle(pageOf(filtered, page, size));
  }

  const query = qs({
    q: params.q?.trim() || undefined,
    health: params.health && params.health !== "all" ? params.health : undefined,
    phase: params.phase && params.phase !== "all" ? params.phase : undefined,
    sort: params.sort,
    page,
    size,
  });
  return apiRequest<Page<ProjectOut>>(`/projects-workspace/projects${query}`);
}

/** Every project, across every page — for the widgets that need the whole portfolio to add up a
 *  number (Overview, Timeline, and every owner/project picker). */
export async function getAllProjects(): Promise<ProjectOut[]> {
  if (USE_FIXTURES) return PROJECTS;
  return fetchAll((page, size) => listProjects({ page, size }));
}

/** `GET /projects-workspace/projects?phase=live&size=1` → `Page.total` — the portfolio's
 *  "Completed" count, from the server's own rollup rather than a client-side scan. */
export async function countCompletedProjects(): Promise<number> {
  const page = await listProjects({ phase: "live", size: 1 });
  return page.total;
}

export async function createProject(input: ProjectIn): Promise<ProjectOut> {
  if (USE_FIXTURES) {
    const owner = MEMBERS.find((m) => m.id === input.owner_ref) ?? MEMBERS[0];
    const created: ProjectOut = {
      id: `fx-project-${input.code}`,
      code: input.code,
      name: input.name,
      client: input.client ?? "",
      owner: owner ? { id: owner.id, name: owner.name, role: owner.role } : { id: "", name: "Unassigned", role: "" },
      phase: input.phase ?? "discovery",
      health: input.health ?? "on_track",
      progress_pct: input.progress_pct ?? 0,
      start_date: input.start_date,
      target_date: input.target_date,
      budget_minor: input.budget_minor ?? 0,
      spent_minor: input.spent_minor ?? 0,
      currency: (input.currency ?? "INR").toUpperCase(),
      team_size: input.team_size ?? 0,
      open_tasks: 0,
      milestone_count: 0,
      summary: input.summary ?? "",
    };
    PROJECTS.push(created);
    return settle(created);
  }
  return apiRequest<ProjectOut>("/projects-workspace/projects", { method: "POST", body: input });
}

export async function updateProject(code: string, input: ProjectUpdate): Promise<ProjectOut> {
  if (USE_FIXTURES) {
    const found = PROJECTS.find((p) => p.code === code);
    if (!found) throw new Error("That project is not part of this organization");
    if (input.name !== undefined) found.name = input.name;
    if (input.client !== undefined) found.client = input.client;
    if ("owner_ref" in input) {
      const owner = MEMBERS.find((m) => m.id === input.owner_ref);
      found.owner = owner ? { id: owner.id, name: owner.name, role: owner.role } : { id: "", name: "Unassigned", role: "" };
    }
    if (input.phase !== undefined) found.phase = input.phase;
    if (input.health !== undefined) found.health = input.health;
    if (input.progress_pct !== undefined) found.progress_pct = input.progress_pct;
    if (input.start_date !== undefined) found.start_date = input.start_date;
    if (input.target_date !== undefined) found.target_date = input.target_date;
    if (input.budget_minor !== undefined) found.budget_minor = input.budget_minor;
    if (input.spent_minor !== undefined) found.spent_minor = input.spent_minor;
    if (input.currency !== undefined) found.currency = input.currency.toUpperCase();
    if (input.team_size !== undefined) found.team_size = input.team_size;
    if (input.summary !== undefined) found.summary = input.summary;
    return settle(found);
  }
  return apiRequest<ProjectOut>(`/projects-workspace/projects/${encodeURIComponent(code)}`, {
    method: "PATCH",
    body: input,
  });
}

export async function deleteProject(code: string): Promise<void> {
  if (USE_FIXTURES) {
    const index = PROJECTS.findIndex((p) => p.code === code);
    if (index >= 0) PROJECTS.splice(index, 1);
    for (let i = MILESTONES.length - 1; i >= 0; i -= 1) {
      if (MILESTONES[i]?.project_code === code) MILESTONES.splice(i, 1);
    }
    for (let i = TASKS.length - 1; i >= 0; i -= 1) {
      if (TASKS[i]?.project_code === code) TASKS.splice(i, 1);
    }
    return settle(undefined);
  }
  await apiRequest<void>(`/projects-workspace/projects/${encodeURIComponent(code)}`, {
    method: "DELETE",
  });
}

/* ------------------------------------------------------------------------ milestones */

export interface ListMilestonesParams {
  page?: number;
  size?: number;
  status?: MilestoneStatus;
  project_code?: string;
}

/** Milestones, fixed order (due date ascending — undated ones last) — the backend has no `sort`
 *  or `q` on this list, so neither does this client. */
export async function listMilestones(
  params: ListMilestonesParams = {},
): Promise<Page<MilestoneOut>> {
  const page = params.page ?? 1;
  const size = params.size ?? DEFAULT_PAGE_SIZE;
  if (USE_FIXTURES) {
    const filtered = MILESTONES.filter(
      (m) =>
        (!params.status || m.status === params.status) &&
        (!params.project_code || m.project_code === params.project_code),
    ).sort((a, b) => {
      if (a.due_date === null && b.due_date === null) return 0;
      if (a.due_date === null) return 1;
      if (b.due_date === null) return -1;
      return a.due_date.localeCompare(b.due_date);
    });
    return settle(pageOf(filtered, page, size));
  }
  const query = qs({ status: params.status, project_code: params.project_code, page, size });
  return apiRequest<Page<MilestoneOut>>(`/projects-workspace/milestones${query}`);
}

/** Every milestone, across every page — for the Overview, Timeline and Milestones screens, none
 *  of which page: they need the whole set to roll up or to filter by status client-side. */
export async function getAllMilestones(params: { project_code?: string } = {}): Promise<
  MilestoneOut[]
> {
  if (USE_FIXTURES) {
    return MILESTONES.filter((m) => !params.project_code || m.project_code === params.project_code);
  }
  return fetchAll((page, size) => listMilestones({ ...params, page, size }));
}

export async function createMilestone(input: MilestoneIn): Promise<MilestoneOut> {
  if (USE_FIXTURES) {
    const project = PROJECTS.find((p) => p.code === input.project_code);
    if (!project) throw new Error("That project is not part of this organization");
    const owner = MEMBERS.find((m) => m.id === input.owner_ref);
    const created: MilestoneOut = {
      id: `fx-milestone-${Date.now()}-${MILESTONES.length}`,
      project_id: project.id,
      project_code: project.code,
      project_name: project.name,
      title: input.title,
      owner: owner ? { id: owner.id, name: owner.name, role: owner.role } : { id: "", name: "Unassigned", role: "" },
      due_date: input.due_date,
      status: input.status ?? "on_track",
      progress_pct: input.progress_pct ?? 0,
    };
    MILESTONES.push(created);
    project.milestone_count += 1;
    return settle(created);
  }
  return apiRequest<MilestoneOut>("/projects-workspace/milestones", {
    method: "POST",
    body: input,
  });
}

export async function updateMilestone(
  id: string,
  input: MilestoneUpdate,
): Promise<MilestoneOut> {
  if (USE_FIXTURES) {
    const found = MILESTONES.find((m) => m.id === id);
    if (!found) throw new Error("That milestone is not part of this organization");
    if (input.title !== undefined) found.title = input.title;
    if ("owner_ref" in input) {
      const owner = MEMBERS.find((m) => m.id === input.owner_ref);
      found.owner = owner ? { id: owner.id, name: owner.name, role: owner.role } : { id: "", name: "Unassigned", role: "" };
    }
    if ("due_date" in input) found.due_date = input.due_date ?? null;
    if (input.status !== undefined) found.status = input.status;
    if (input.progress_pct !== undefined) found.progress_pct = input.progress_pct;
    return settle(found);
  }
  return apiRequest<MilestoneOut>(`/projects-workspace/milestones/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: input,
  });
}

export async function deleteMilestone(id: string): Promise<void> {
  if (USE_FIXTURES) {
    const index = MILESTONES.findIndex((m) => m.id === id);
    if (index >= 0) {
      const [removed] = MILESTONES.splice(index, 1);
      const project = removed && PROJECTS.find((p) => p.code === removed.project_code);
      if (project) project.milestone_count = Math.max(0, project.milestone_count - 1);
    }
    return settle(undefined);
  }
  await apiRequest<void>(`/projects-workspace/milestones/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}

/* ------------------------------------------------------------------------ tasks (the board) */

export interface ListTasksParams {
  page?: number;
  size?: number;
  stage?: TaskStage;
  project_code?: string;
  /** `"me"` narrows to cards assigned to the caller, resolved server-side against the delivery
   *  roster — the same filter `/projects-workspace/tasks?assignee=me` applies. */
  assignee?: "me";
}

export async function listTasks(params: ListTasksParams = {}): Promise<Page<TaskOut>> {
  const page = params.page ?? 1;
  const size = params.size ?? DEFAULT_PAGE_SIZE;
  if (USE_FIXTURES) {
    // Fixture mode has no signed-in "me" to resolve against the roster, so `assignee=me` returns
    // nothing — the same honest empty page the live route answers when the caller has no linked
    // member row, rather than guessing at one.
    const filtered = params.assignee === "me"
      ? []
      : TASKS.filter(
          (t) =>
            (!params.stage || t.stage === params.stage) &&
            (!params.project_code || t.project_code === params.project_code),
        );
    return settle(pageOf(filtered, page, size));
  }
  const query = qs({
    stage: params.stage,
    project_code: params.project_code,
    assignee: params.assignee,
    page,
    size,
  });
  return apiRequest<Page<TaskOut>>(`/projects-workspace/tasks${query}`);
}

/** Every card, across every page — what the board renders; a board that silently drops card 101
 *  is worse than one extra request. */
export async function getAllTasks(params: { project_code?: string } = {}): Promise<TaskOut[]> {
  if (USE_FIXTURES) {
    return TASKS.filter((t) => !params.project_code || t.project_code === params.project_code);
  }
  return fetchAll((page, size) => listTasks({ ...params, page, size }));
}

/** `GET /projects-workspace/tasks?assignee=me` — resolved server-side to the caller's own cards,
 *  so one bounded call gives the Overview's "My tasks" list, never a scan. */
export async function listMyTasks(size: number): Promise<Page<TaskOut>> {
  return listTasks({ assignee: "me", size });
}

export async function createTask(input: TaskIn): Promise<TaskOut> {
  if (USE_FIXTURES) {
    const project = PROJECTS.find((p) => p.code === input.project_code);
    if (!project) throw new Error("That project is not part of this organization");
    const assignee = MEMBERS.find((m) => m.id === input.assignee_ref);
    const created: TaskOut = {
      id: `fx-task-${Date.now()}-${TASKS.length}`,
      project_id: project.id,
      project_code: project.code,
      title: input.title,
      assignee: assignee
        ? { id: assignee.id, name: assignee.name, role: assignee.role }
        : { id: "", name: "Unassigned", role: "" },
      stage: input.stage ?? "backlog",
      priority: input.priority ?? "medium",
      due_date: input.due_date ?? null,
      points: input.points ?? 0,
    };
    TASKS.push(created);
    return settle(created);
  }
  return apiRequest<TaskOut>("/projects-workspace/tasks", { method: "POST", body: input });
}

export async function updateTask(id: string, input: TaskUpdate): Promise<TaskOut> {
  if (USE_FIXTURES) {
    const found = TASKS.find((t) => t.id === id);
    if (!found) throw new Error("That task is not part of this organization");
    if (input.title !== undefined) found.title = input.title;
    if ("assignee_ref" in input) {
      const assignee = MEMBERS.find((m) => m.id === input.assignee_ref);
      found.assignee = assignee
        ? { id: assignee.id, name: assignee.name, role: assignee.role }
        : { id: "", name: "Unassigned", role: "" };
    }
    if (input.stage !== undefined) found.stage = input.stage;
    if (input.priority !== undefined) found.priority = input.priority;
    if ("due_date" in input) found.due_date = input.due_date ?? null;
    if (input.points !== undefined) found.points = input.points;
    return settle(found);
  }
  return apiRequest<TaskOut>(`/projects-workspace/tasks/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: input,
  });
}

export async function deleteTask(id: string): Promise<void> {
  if (USE_FIXTURES) {
    const index = TASKS.findIndex((t) => t.id === id);
    if (index >= 0) TASKS.splice(index, 1);
    return settle(undefined);
  }
  await apiRequest<void>(`/projects-workspace/tasks/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}

/* ------------------------------------------------------------------------ members (the roster) */

export interface ListMembersParams {
  page?: number;
  size?: number;
  q?: string;
}

export async function listMembers(params: ListMembersParams = {}): Promise<Page<MemberOut>> {
  const page = params.page ?? 1;
  const size = params.size ?? DEFAULT_PAGE_SIZE;
  if (USE_FIXTURES) {
    const q = params.q?.trim().toLowerCase() ?? "";
    const filtered = MEMBERS.filter(
      (m) => q === "" || m.name.toLowerCase().includes(q) || m.id.toLowerCase().includes(q),
    );
    return settle(pageOf(filtered, page, size));
  }
  const query = qs({ q: params.q?.trim() || undefined, page, size });
  return apiRequest<Page<MemberOut>>(`/projects-workspace/members${query}`);
}

/** The whole roster, one flattened call — the source for every owner/assignee picker. */
export async function getAllMembers(): Promise<MemberOut[]> {
  if (USE_FIXTURES) return MEMBERS;
  return fetchAll((page, size) => listMembers({ page, size }));
}

export async function createMember(input: MemberIn): Promise<MemberOut> {
  if (USE_FIXTURES) {
    const created: MemberOut = {
      id: input.ref,
      name: input.name,
      role: input.role ?? "",
      directory_id: input.directory_id ?? null,
    };
    MEMBERS.push(created);
    return settle(created);
  }
  return apiRequest<MemberOut>("/projects-workspace/members", { method: "POST", body: input });
}

export async function updateMember(ref: string, input: MemberUpdate): Promise<MemberOut> {
  if (USE_FIXTURES) {
    const found = MEMBERS.find((m) => m.id === ref);
    if (!found) throw new Error("That member is not part of this organization");
    if (input.name !== undefined) found.name = input.name;
    if (input.role !== undefined) found.role = input.role;
    if ("directory_id" in input) found.directory_id = input.directory_id ?? null;
    // Keep every embedded Person projection in sync — the same reason `services.py` updates a
    // stored `Person` snapshot nowhere: this app derives owner/assignee display from `MEMBERS`.
    return settle(found);
  }
  return apiRequest<MemberOut>(`/projects-workspace/members/${encodeURIComponent(ref)}`, {
    method: "PATCH",
    body: input,
  });
}

export async function deleteMember(ref: string): Promise<void> {
  if (USE_FIXTURES) {
    const index = MEMBERS.findIndex((m) => m.id === ref);
    if (index >= 0) MEMBERS.splice(index, 1);
    return settle(undefined);
  }
  await apiRequest<void>(`/projects-workspace/members/${encodeURIComponent(ref)}`, {
    method: "DELETE",
  });
}
