/**
 * Typed PulseBoard API client.
 *
 * Live by default: every function calls the gateway at the same-origin `/api/v1/projects`,
 * `/api/v1/timesheets` and `/api/v1/standups` prefixes (Vite proxies them to their respective
 * services in dev) with the bearer token from the session. PulseBoard has no single service —
 * projects/tasks/people live in the project service, timesheets and standups in their own —
 * so unlike a single-prefix product client this one spans three. Set `VITE_USE_FIXTURES=true`
 * to render the whole app from `fixtures.ts` with no login — handy for offline design work.
 */

import { apiRequest } from "@opsgrids/client";
import { PEOPLE } from "@/data/people";
import {
  PROJECTS,
  STANDUPS,
  TASKS,
  TIMESHEETS,
  TODAY,
  WEEK_DATES,
  WEEK_START,
} from "./fixtures";
import type {
  Person,
  Project,
  ProjectStatus,
  StandupEntry,
  Task,
  TimesheetRow,
} from "./types";

/** Default source. Live unless `VITE_USE_FIXTURES=true` pins the app to fixtures.
 *  Dev builds only: `import.meta.env.DEV` compiles to `false` in production, so a stray
 *  CI/production env var can never ship fixture mode — an app rendering fabricated data
 *  with the "Sample data" markers suppressed. */
export const USE_FIXTURES = import.meta.env.DEV && import.meta.env.VITE_USE_FIXTURES === "true";

/** The current week's Monday and its seven ISO dates — shared by the Overview and Timesheets. */
export { WEEK_START, WEEK_DATES, TODAY };

/** Small artificial delay so loading states are exercised in fixture mode. */
function settle<T>(value: T, ms = 240): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(value), ms));
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
  return apiRequest<Project[]>(`/projects?${query.toString()}`);
}

/** Every project (unfiltered) — powers the Overview rollups. */
export async function getProjects(): Promise<Project[]> {
  if (USE_FIXTURES) return settle(PROJECTS);
  return apiRequest<Project[]>(`/projects`);
}

/** Every task on the board. */
export async function getTasks(): Promise<Task[]> {
  if (USE_FIXTURES) return settle(TASKS);
  return apiRequest<Task[]>(`/projects/tasks`);
}

/** The delivery-team roster. */
export async function getPeople(): Promise<Person[]> {
  if (USE_FIXTURES) return settle(PEOPLE);
  return apiRequest<Person[]>(`/projects/people`);
}

/** The current week's timesheets, one row per person. */
export async function getTimesheets(): Promise<TimesheetRow[]> {
  if (USE_FIXTURES) return settle(TIMESHEETS);
  return apiRequest<TimesheetRow[]>(`/timesheets?week=${WEEK_START}`);
}

/** Standup entries for a given day (defaults to today). */
export async function getStandups(date: string = TODAY): Promise<StandupEntry[]> {
  if (USE_FIXTURES) return settle(STANDUPS.filter((s) => s.date === date));
  return apiRequest<StandupEntry[]>(`/standups?date=${date}`);
}
