/**
 * Typed Projects API client.
 *
 * Fixtures-first: the `projects` service backend is a skeleton on this branch, so the app renders
 * entirely from `fixtures.ts` by default. Flip `VITE_USE_FIXTURES=false` once the service ships and
 * every call switches to the gateway at the same-origin `/api/v1` prefix (Vite proxies it to
 * `http://localhost:8100` in dev) with the bearer token from the session — no other code changes.
 */

import { apiRequest } from "./http";
import { MILESTONES, PROJECTS, TASKS } from "./fixtures";
import type {
  MilestoneOut,
  Page,
  ProjectHealth,
  ProjectOut,
  ProjectPhase,
  TaskOut,
} from "./types";

/** Default source. Fixtures unless `VITE_USE_FIXTURES=false` points the app at the gateway.
 *  Dev builds only: `import.meta.env.DEV` compiles to `false` in production, so a stray
 *  CI/production env var can never ship fixture mode — an app rendering fabricated data
 *  with the "Sample data" markers suppressed. */
export const USE_FIXTURES = import.meta.env.DEV && import.meta.env.VITE_USE_FIXTURES !== "false";

/** True when the app is talking to the real gateway. */
export const LIVE = !USE_FIXTURES;

/** A generous page size — one request returns the whole (small) portfolio for this org. */
const FULL_PAGE = 100;

/** Small artificial delay so loading states are exercised in fixture mode. */
function settle<T>(value: T, ms = 240): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(value), ms));
}

export interface ListProjectsParams {
  search?: string;
  health?: ProjectHealth | "all";
  phase?: ProjectPhase | "all";
}

export async function listProjects(
  params: ListProjectsParams = {},
): Promise<Page<ProjectOut>> {
  if (USE_FIXTURES) {
    const q = params.search?.trim().toLowerCase() ?? "";
    const filtered = PROJECTS.filter((p) => {
      const matchesQuery =
        q === "" ||
        p.name.toLowerCase().includes(q) ||
        p.code.toLowerCase().includes(q) ||
        p.client.toLowerCase().includes(q) ||
        p.owner.name.toLowerCase().includes(q);
      const matchesHealth =
        !params.health || params.health === "all" || p.health === params.health;
      const matchesPhase =
        !params.phase || params.phase === "all" || p.phase === params.phase;
      return matchesQuery && matchesHealth && matchesPhase;
    });
    return settle({ items: filtered, total: filtered.length });
  }

  const query = new URLSearchParams({ size: String(FULL_PAGE) });
  if (params.search?.trim()) query.set("q", params.search.trim());
  if (params.health && params.health !== "all") query.set("health", params.health);
  if (params.phase && params.phase !== "all") query.set("phase", params.phase);
  return apiRequest<Page<ProjectOut>>(`/projects?${query.toString()}`);
}

/** The whole portfolio (unfiltered) — powers the overview rollups and the timeline. */
export async function getAllProjects(): Promise<Page<ProjectOut>> {
  if (USE_FIXTURES) return settle({ items: PROJECTS, total: PROJECTS.length });
  return apiRequest<Page<ProjectOut>>(`/projects?size=${FULL_PAGE}`);
}

/** Every milestone across the portfolio, for the overview and the milestones screen. */
export async function listMilestones(): Promise<Page<MilestoneOut>> {
  if (USE_FIXTURES) return settle({ items: MILESTONES, total: MILESTONES.length });
  return apiRequest<Page<MilestoneOut>>(`/milestones?size=${FULL_PAGE}`);
}

/** Every open/closed card on the workflow board. */
export async function listTasks(): Promise<Page<TaskOut>> {
  if (USE_FIXTURES) return settle({ items: TASKS, total: TASKS.length });
  return apiRequest<Page<TaskOut>>(`/tasks?size=${FULL_PAGE}`);
}
