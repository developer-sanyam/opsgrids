/**
 * Typed HRMS API client.
 *
 * Live by default: every function calls the gateway at the same-origin `/api/v1` prefix (Vite
 * proxies it to `http://localhost:8100` in dev) with the bearer token from the session. Set
 * `VITE_USE_FIXTURES=true` to render the whole app from `fixtures.ts` with no login — handy for
 * offline design work.
 *
 * Employee and approval data are live (`services/employee`, `services/approval` — see
 * `api/approvals.ts`). **The daily punch roster has no live equivalent yet** — `AttendanceOut`
 * carries a record's own punch and its correction state, but nothing shaped like the roster this
 * screen renders (names, hours, a whole day's table) exists on the wire — so `listAttendance`
 * stays representative, and the UI flags it with a "Sample data" chip whenever the app is running
 * live (`ATTENDANCE_IS_SAMPLE`). Because the roster's ids are not real attendance record ids,
 * `requestRegularization` is simulated against it in every mode, mirroring the live service's own
 * validation exactly (`services/attendance/app/services.request_regularization`,
 * `app/domain.ALLOWED_TRANSITIONS`) so every failure branch in screens spec §3.1 is exercised
 * without a login. It is written against the real wire contract throughout, so swapping in a real
 * roster later is a one-line change at the call site, not a rewrite of this function.
 */

import { ApiError, apiRequest } from "@opsgrids/client";
import type { ProblemJson } from "@opsgrids/client";

import type { FieldError } from "@/lib/problem";
import { ATTENDANCE_TODAY, EMPLOYEES, FIXTURE_ALLOWED_TRANSITIONS } from "./fixtures";
import type {
  AttendanceRecord,
  EmployeeOut,
  Page,
  RegularizationRequestIn,
  RegularizationRequestOut,
} from "./types";

/** Default source. Live unless `VITE_USE_FIXTURES=true` pins the app to fixtures.
 *  Dev builds only: `import.meta.env.DEV` compiles to `false` in production, so a stray
 *  CI/production env var can never ship fixture mode — an app rendering fabricated data
 *  with the "Sample data" markers suppressed. */
export const USE_FIXTURES = import.meta.env.DEV && import.meta.env.VITE_USE_FIXTURES === "true";

/** True when the app is talking to the real gateway. */
export const LIVE = !USE_FIXTURES;

/**
 * Attendance is representative, not live, until the punch roster ships. When running live the UI
 * shows a "Sample data" chip on every attendance-derived surface; in fixture mode the whole app
 * is illustrative, so nothing is singled out.
 */
export const ATTENDANCE_IS_SAMPLE = LIVE;

/** A generous page size — one request returns the whole (small) directory for this org. */
const FULL_PAGE = 100;

/** Small artificial delay so loading states are exercised in fixture (and sample) mode. */
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

/**
 * The whole employee directory for this org (unfiltered) — powers every dashboard rollup:
 * KPIs, headcount by department, and the workforce mix are all derived from this one payload.
 */
export async function getAllEmployees(): Promise<Page<EmployeeOut>> {
  if (USE_FIXTURES) return settle({ items: EMPLOYEES, total: EMPLOYEES.length });
  return apiRequest<Page<EmployeeOut>>(`/employees?size=${FULL_PAGE}`);
}

/** One employee, for the employee detail page (`/employees/:employeeId`). */
export async function getEmployee(employeeId: string): Promise<EmployeeOut> {
  if (USE_FIXTURES) {
    const emp = EMPLOYEES.find((e) => e.id === employeeId);
    if (!emp) throw new ApiError(404, "Employee not found", { detail: "Employee not found" });
    return settle(emp);
  }
  return apiRequest<EmployeeOut>(`/employees/${employeeId}`);
}

/* --------------------------------------------------------------- attendance (sample roster) */

/** The mutable, in-session copy of the sample roster. `listAttendance` reads it and
 *  `requestRegularization` (in sample mode) writes to it, so a correction persists for the rest
 *  of the session — the same "representative but internally consistent" contract the rest of the
 *  sample data keeps. */
let sampleRoster: AttendanceRecord[] = ATTENDANCE_TODAY.map((r) => ({ ...r }));

export async function listAttendance(workDate: string): Promise<AttendanceRecord[]> {
  // No live punch roster on this org yet (see the module docstring) — return the representative
  // day for any date so the screen renders rather than erroring.
  return settle(sampleRoster.map((r) => ({ ...r, work_date: workDate })));
}

function problemError(status: number, detail: string, errors?: FieldError[]): ApiError {
  const body: ProblemJson & { errors?: FieldError[] } = {
    detail,
    ...(errors ? { errors } : {}),
  };
  return new ApiError(status, detail, body);
}

/**
 * `POST /attendance/{id}/regularization-requests` — self-service, own record only. Simulated
 * against `sampleRoster` (see the module docstring), mirroring the server's own rules so every
 * failure branch in §3.1 is exercised without a login.
 */
export async function requestRegularization(
  recordId: string,
  payload: RegularizationRequestIn,
): Promise<RegularizationRequestOut> {
  const record = sampleRoster.find((r) => r.id === recordId);
  if (!record) {
    throw problemError(404, "That attendance record isn't available any more.");
  }
  if (record.regularization?.status === "pending") {
    throw problemError(409, "A regularization request for this record is already pending");
  }
  if (!payload.reason.trim()) {
    throw problemError(422, "Request payload failed validation", [
      { field: "reason", code: "string_too_short", message: "Reason cannot be blank" },
    ]);
  }
  if (payload.reason.length > 500) {
    throw problemError(422, "Request payload failed validation", [
      { field: "reason", code: "string_too_long", message: "Reason must be at most 500 characters" },
    ]);
  }
  const allowed = FIXTURE_ALLOWED_TRANSITIONS[record.status];
  if (!allowed.includes(payload.requested_status)) {
    throw problemError(
      422,
      `An attendance record cannot move from '${record.status}' to '${payload.requested_status}'`,
    );
  }

  const reg: RegularizationRequestOut = {
    id: `${record.id}-reg-${Date.now()}`,
    attendance_id: record.id,
    employee_id: record.employee_id,
    requested_status: payload.requested_status,
    reason: payload.reason,
    status: "pending",
    approval_request_id: null,
    approval_opened_at: null,
    approver_employee_id: null,
    approver_name: null,
    decided_at: null,
    decision_comment: null,
    decided_by_name: null,
    failure_reason: null,
    created_at: new Date().toISOString(),
    approver_unassigned: null,
    location_code: record.location_code,
  };
  sampleRoster = sampleRoster.map((r) =>
    r.id === recordId ? { ...r, regularization: reg } : r,
  );
  return settle(reg);
}

/** Test-only: restores the sample roster to its initial fixture state between test cases. */
export function __resetSampleRosterForTests(): void {
  sampleRoster = ATTENDANCE_TODAY.map((r) => ({ ...r }));
}
