/**
 * Typed HRMS API client.
 *
 * Live by default: every function calls the gateway at the same-origin `/api/v1` prefix (Vite
 * proxies it to `http://localhost:8100` in dev) with the bearer token from the session. Set
 * `VITE_USE_FIXTURES=true` to render the whole app from `fixtures.ts` with no login — handy for
 * offline design work.
 *
 * Employee data is live. **Attendance has no service yet**, so the attendance calls and the
 * attendance-derived dashboard widgets always return representative data; the UI flags those with
 * a "Sample data" chip whenever the app is running live (`ATTENDANCE_IS_SAMPLE`).
 */

import { apiRequest } from "./http";
import {
  ATTENDANCE_TODAY,
  DASHBOARD,
  EMPLOYEES,
  RECENT_JOINERS,
} from "./fixtures";
import type {
  AttendanceRecord,
  DashboardStats,
  EmployeeOut,
  Page,
} from "./types";

/** Default source. Live unless `VITE_USE_FIXTURES=true` pins the app to fixtures. */
export const USE_FIXTURES = import.meta.env.VITE_USE_FIXTURES === "true";

/** True when the app is talking to the real gateway. */
export const LIVE = !USE_FIXTURES;

/**
 * Attendance is representative, not live, until the attendance service ships. When running live
 * the UI shows a "Sample data" chip on every attendance-derived surface; in fixture mode the whole
 * app is illustrative, so nothing is singled out.
 */
export const ATTENDANCE_IS_SAMPLE = LIVE;

/** A generous page size — one request returns the whole (small) directory for this org. */
const FULL_PAGE = 100;

/** Small artificial delay so loading states are exercised in fixture mode. */
function settle<T>(value: T, ms = 220): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(value), ms));
}

export interface ListEmployeesParams {
  search?: string;
  status?: EmployeeOut["status"] | "all";
}

export async function listEmployees(
  params: ListEmployeesParams = {},
): Promise<Page<EmployeeOut>> {
  if (USE_FIXTURES) {
    const q = params.search?.trim().toLowerCase() ?? "";
    const filtered = EMPLOYEES.filter((e) => {
      const matchesQuery =
        q === "" ||
        e.full_name.toLowerCase().includes(q) ||
        e.employee_no.toLowerCase().includes(q) ||
        (e.designation?.toLowerCase().includes(q) ?? false);
      const matchesStatus =
        !params.status || params.status === "all" || e.status === params.status;
      return matchesQuery && matchesStatus;
    });
    return settle({ items: filtered, total: filtered.length });
  }

  const query = new URLSearchParams({ size: String(FULL_PAGE) });
  if (params.search?.trim()) query.set("q", params.search.trim());
  if (params.status && params.status !== "all") query.set("status", params.status);
  return apiRequest<Page<EmployeeOut>>(`/employees?${query.toString()}`);
}

/** All employees for this org (unfiltered), newest joiners first — powers the dashboard rollups. */
async function allEmployees(): Promise<EmployeeOut[]> {
  if (USE_FIXTURES) return EMPLOYEES;
  const page = await apiRequest<Page<EmployeeOut>>(`/employees?size=${FULL_PAGE}`);
  return page.items;
}

export async function getDashboardStats(): Promise<DashboardStats> {
  if (USE_FIXTURES) return settle(DASHBOARD);

  // Total Employees is live; the attendance-derived cards and the weekly chart stay
  // representative (there is no attendance service yet) and the UI marks them as sample.
  const total = (await apiRequest<Page<EmployeeOut>>(`/employees?size=1`)).total;
  return {
    ...DASHBOARD,
    cards: DASHBOARD.cards.map((card) =>
      card.key === "total_employees" ? { ...card, value: total } : card,
    ),
  };
}

export async function getRecentJoiners(): Promise<EmployeeOut[]> {
  if (USE_FIXTURES) return settle(RECENT_JOINERS);
  const people = await allEmployees();
  return [...people]
    .filter((e) => e.date_of_joining !== null)
    .sort((a, b) => (a.date_of_joining! < b.date_of_joining! ? 1 : -1))
    .slice(0, 5);
}

export async function listAttendance(workDate: string): Promise<AttendanceRecord[]> {
  // No attendance service on this org yet — return the representative day for any date so the
  // screen renders rather than erroring. The UI labels it "Sample data" when running live.
  return settle(ATTENDANCE_TODAY.map((r) => ({ ...r, work_date: workDate })));
}
