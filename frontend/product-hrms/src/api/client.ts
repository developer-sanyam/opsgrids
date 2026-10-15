/**
 * Typed HRMS API client.
 *
 * Live by default: every function calls the gateway at the same-origin `/api/v1` prefix (Vite
 * proxies it to the gateway in dev) with the bearer token from the session. Set
 * `VITE_USE_FIXTURES=true` (dev builds only) to render the whole app from `fixtures.ts` with no
 * login — handy for offline design work; never reachable in a production build
 * (`import.meta.env.DEV` compiles to `false` there).
 *
 * `employee`, `attendance`, `shifts` and `geofences` are all live services — nothing in this
 * module renders fabricated data while `LIVE` is true. The one client-side construction is
 * `listAttendanceRoster`: `AttendanceOut` carries only `employee_id`, so the roster table's name
 * column is a join against `GET /employees`, done here rather than by the screen.
 */

import { ApiError, apiRequest, buildQuery as qs, settle } from "@opsgrids/client";

import {
  ATTENDANCE_TODAY,
  __createFixtureEmployee,
  __createFixtureGeofence,
  __createFixtureShift,
  __deleteFixtureGeofence,
  __deleteFixtureShift,
  __getFixtureEmployee,
  __listFixtureEmployees,
  __listFixtureGeofences,
  __listFixtureReports,
  __listFixtureShifts,
  __transitionFixtureEmployee,
  __unmaskFixturePii,
  __updateFixtureEmployee,
  __updateFixtureGeofence,
  __updateFixtureShift,
} from "./fixtures";
import type {
  AttendanceOut,
  AttendanceRecord,
  AttendanceStatus,
  CheckInIn,
  CheckOutIn,
  EmployeeIn,
  EmployeeOut,
  EmployeePatch,
  EmployeePiiUnmasked,
  EmployeeStatus,
  EmployeeTransitionIn,
  GeofenceIn,
  GeofenceOut,
  GeofencePatch,
  Paged,
  PiiUnmaskIn,
  RegularizationRequestIn,
  RegularizationRequestOut,
  RegularizeIn,
  ShiftIn,
  ShiftOut,
  ShiftPatch,
} from "./types";

/** Default source. Live unless `VITE_USE_FIXTURES=true` pins the app to fixtures.
 *  Dev builds only: `import.meta.env.DEV` compiles to `false` in production, so a stray
 *  CI/production env var can never ship fixture mode — an app rendering fabricated data
 *  with the "Sample data" markers suppressed. */
export const USE_FIXTURES = import.meta.env.DEV && import.meta.env.VITE_USE_FIXTURES === "true";

/** True when the app is talking to the real gateway. */
export const LIVE = !USE_FIXTURES;

/* --------------------------------------------------------------------------------- employees */

export interface ListEmployeesParams {
  q?: string | undefined;
  departmentId?: string | undefined;
  status?: EmployeeStatus | "all" | undefined;
  page?: number | undefined;
  size?: number | undefined;
  /** Aborts the request: the import/export dialog cancels a lookup, a load or an export with it. */
  signal?: AbortSignal | undefined;
}

/** `GET /employees` — server search, status and department filter, real paging. There is no
 *  `sort` param on this endpoint; order is fixed server-side by `full_name`. */
export async function listEmployees(params: ListEmployeesParams = {}): Promise<Paged<EmployeeOut>> {
  const { q, departmentId, status, page = 1, size = 25, signal } = params;
  if (USE_FIXTURES) {
    const needle = q?.trim().toLowerCase() ?? "";
    const filtered = __listFixtureEmployees().filter((e) => {
      const matchesQuery =
        needle === "" ||
        e.full_name.toLowerCase().includes(needle) ||
        e.employee_no.toLowerCase().includes(needle) ||
        (e.designation?.toLowerCase().includes(needle) ?? false);
      const matchesStatus = !status || status === "all" || e.status === status;
      const matchesDept = !departmentId || e.department_id === departmentId;
      return matchesQuery && matchesStatus && matchesDept;
    });
    const start = (page - 1) * size;
    const items = filtered.slice(start, start + size);
    return settle({
      items,
      total: filtered.length,
      page,
      size,
      pages: Math.max(1, Math.ceil(filtered.length / size)),
    });
  }
  const query = qs({
    q,
    department_id: departmentId,
    status: status && status !== "all" ? status : undefined,
    page,
    size,
  });
  return apiRequest<Paged<EmployeeOut>>(`/employees${query}`, { signal });
}

/** One employee, for the employee detail page (`/employees/:employeeId`). */
export async function getEmployee(employeeId: string): Promise<EmployeeOut> {
  if (USE_FIXTURES) {
    const emp = __getFixtureEmployee(employeeId);
    if (!emp) {
      throw new ApiError(404, "That employee isn't available to you", {
        detail: "It may have been removed, or you may not have access.",
      });
    }
    return settle(emp);
  }
  return apiRequest<EmployeeOut>(`/employees/${employeeId}`);
}

/**
 * Every employee in the org, paged through the server — powers the dashboard's rollups (KPIs,
 * headcount by department, workforce mix). Bounded so a runaway org can never spin this forever;
 * a screen that needs more than that should not be loading the whole directory into memory.
 */
export async function getAllEmployees(): Promise<Paged<EmployeeOut>> {
  const first = await listEmployees({ page: 1, size: 200 });
  if (first.pages <= 1) return first;
  const items = [...first.items];
  const maxPages = Math.min(first.pages, 50);
  for (let page = 2; page <= maxPages; page += 1) {
    const next = await listEmployees({ page, size: 200 });
    items.push(...next.items);
  }
  return { items, total: first.total, page: 1, size: items.length, pages: 1 };
}

/** `POST /employees` — `hrms.people.employee.write` at ORG. `membership_id` is never sent: the
 *  employee↔member link belongs to account (ADR-0008 §3). */
export async function createEmployee(body: EmployeeIn): Promise<EmployeeOut> {
  if (USE_FIXTURES) return settle(__createFixtureEmployee(body));
  return apiRequest<EmployeeOut>("/employees", { method: "POST", body });
}

/** `PATCH /employees/{id}` — only the keys present are touched; an explicit `null` clears a
 *  field, an absent key leaves it alone. */
export async function updateEmployee(employeeId: string, body: EmployeePatch): Promise<EmployeeOut> {
  if (USE_FIXTURES) return settle(__updateFixtureEmployee(employeeId, body));
  return apiRequest<EmployeeOut>(`/employees/${employeeId}`, { method: "PATCH", body });
}

/** `POST /employees/{id}/transition` — the only way an employee's lifecycle status moves. */
export async function transitionEmployee(
  employeeId: string,
  body: EmployeeTransitionIn,
): Promise<EmployeeOut> {
  if (USE_FIXTURES) return settle(__transitionFixtureEmployee(employeeId, body));
  return apiRequest<EmployeeOut>(`/employees/${employeeId}/transition`, { method: "POST", body });
}

/** `POST /employees/{id}/pii/unmask` — `hrms.people.employee.read_sensitive`, no second factor
 *  (ADR-0023 addendum, 2026-10-15). Writes an audit row on every call. */
export async function unmaskEmployeePii(
  employeeId: string,
  body: PiiUnmaskIn,
): Promise<EmployeePiiUnmasked> {
  if (USE_FIXTURES) return settle(__unmaskFixturePii(employeeId, body));
  return apiRequest<EmployeePiiUnmasked>(`/employees/${employeeId}/pii/unmask`, {
    method: "POST",
    body,
  });
}

/** `GET /employees/{id}/reports` — direct reports, by name, unpaged. */
export async function listEmployeeReports(employeeId: string): Promise<EmployeeOut[]> {
  if (USE_FIXTURES) return settle(__listFixtureReports(employeeId));
  return apiRequest<EmployeeOut[]>(`/employees/${employeeId}/reports`);
}

/* --------------------------------------------------------------------------------- attendance */

export interface AttendanceListParams {
  employeeId?: string | undefined;
  dateFrom?: string | undefined;
  dateTo?: string | undefined;
  status?: AttendanceStatus | undefined;
  page?: number | undefined;
  size?: number | undefined;
}

/** `GET /attendance` — the raw wire response, no join. `employee_id` is the only identity on a
 *  row; use `listAttendanceRoster` for a table that needs a name. */
export async function listAttendance(params: AttendanceListParams = {}): Promise<Paged<AttendanceOut>> {
  const { employeeId, dateFrom, dateTo, status, page = 1, size = 25 } = params;
  const query = qs({
    employee_id: employeeId,
    date_from: dateFrom,
    date_to: dateTo,
    status,
    page,
    size,
  });
  return apiRequest<Paged<AttendanceOut>>(`/attendance${query}`);
}

/** One attendance record, with `regularization` and `allowed_transitions`. */
export async function getAttendanceRecord(id: string): Promise<AttendanceOut> {
  return apiRequest<AttendanceOut>(`/attendance/${id}`);
}

/** Every employee in the org, id-keyed, paged through the server so a directory over 200 people
 *  is never silently truncated. Used only to join a name onto an attendance row — screens that
 *  render the directory itself page through `listEmployees` directly instead of loading it whole. */
async function loadEmployeeDirectory(): Promise<Map<string, EmployeeOut>> {
  const byId = new Map<string, EmployeeOut>();
  let page = 1;
  for (;;) {
    const result = await listEmployees({ page, size: 200 });
    for (const emp of result.items) byId.set(emp.id, emp);
    if (page >= result.pages || result.items.length === 0) break;
    page += 1;
  }
  return byId;
}

function toRow(record: AttendanceOut, emp: EmployeeOut | null): AttendanceRecord {
  const checkOut = record.check_out_at;
  const worked =
    checkOut !== null
      ? Math.round((new Date(checkOut).getTime() - new Date(record.check_in_at).getTime()) / 60000)
      : null;
  return {
    id: record.id,
    employee_id: record.employee_id,
    employee_no: emp?.employee_no ?? "—",
    full_name: emp?.full_name ?? "Employee record unavailable",
    department_id: emp?.department_id ?? null,
    job_title: emp?.job_title ?? null,
    work_date: record.work_date,
    check_in: record.check_in_at,
    check_out: record.check_out_at,
    worked_minutes: worked,
    source: record.source,
    work_mode: record.work_mode,
    status: record.status,
    location_code: record.location_code,
    regularization: record.regularization,
    allowed_transitions: record.allowed_transitions,
  };
}

export interface RosterParams {
  page?: number | undefined;
  size?: number | undefined;
}

/** The day's punch roster, joined with the employee it belongs to — `AttendanceOut` alone has no
 *  name to render. A row whose `employee_id` does not resolve renders "Employee record
 *  unavailable", never a blank. */
export async function listAttendanceRoster(
  date: string,
  { page = 1, size = 50 }: RosterParams = {},
): Promise<Paged<AttendanceRecord>> {
  if (USE_FIXTURES) {
    const items = ATTENDANCE_TODAY.map((r) => ({ ...r, work_date: date }));
    const start = (page - 1) * size;
    const slice = items.slice(start, start + size);
    return settle({
      items: slice,
      total: items.length,
      page,
      size,
      pages: Math.max(1, Math.ceil(items.length / size)),
    });
  }
  const [result, directory] = await Promise.all([
    listAttendance({ dateFrom: date, dateTo: date, page, size }),
    loadEmployeeDirectory(),
  ]);
  return {
    items: result.items.map((r) => toRow(r, directory.get(r.employee_id) ?? null)),
    total: result.total,
    page: result.page,
    size: result.size,
    pages: result.pages,
  };
}

export interface AttendanceDaySummary {
  /** Every punch on record for the day, any status. */
  total: number;
  /** `present` + `half_day`. */
  presentOrHalfDay: number;
  absent: number;
}

/** Today's attendance rate and absence count, from the server's own status-filtered totals
 *  (`?status=…&size=1` → `Page.total`) rather than one capped page of raw punches. Correct no
 *  matter how many employees punch in — the Dashboard KPI used to read a single `size:200` page,
 *  which silently undercounted once an org passed 200 punches in a day. */
export async function getAttendanceDaySummary(date: string): Promise<AttendanceDaySummary> {
  if (USE_FIXTURES) {
    const items = ATTENDANCE_TODAY;
    return {
      total: items.length,
      presentOrHalfDay: items.filter((r) => r.status === "present" || r.status === "half_day")
        .length,
      absent: items.filter((r) => r.status === "absent").length,
    };
  }
  const [total, present, halfDay, absent] = await Promise.all([
    listAttendance({ dateFrom: date, dateTo: date, size: 1 }),
    listAttendance({ dateFrom: date, dateTo: date, status: "present", size: 1 }),
    listAttendance({ dateFrom: date, dateTo: date, status: "half_day", size: 1 }),
    listAttendance({ dateFrom: date, dateTo: date, status: "absent", size: 1 }),
  ]);
  return {
    total: total.total,
    presentOrHalfDay: present.total + halfDay.total,
    absent: absent.total,
  };
}

/** `POST /attendance/check-in` — self-service; there is no `employee_id` in the body, the caller
 *  punches their own record. */
export async function checkIn(body: CheckInIn): Promise<AttendanceOut> {
  return apiRequest<AttendanceOut>("/attendance/check-in", { method: "POST", body });
}

/** `POST /attendance/check-out`. */
export async function checkOut(body: CheckOutIn): Promise<AttendanceOut> {
  return apiRequest<AttendanceOut>("/attendance/check-out", { method: "POST", body });
}

/** `POST /attendance/{id}/regularization-requests` — self-service, own record only. Routes
 *  through approvals; the decision lands via the approval consumer. */
export async function requestRegularization(
  recordId: string,
  body: RegularizationRequestIn,
): Promise<RegularizationRequestOut> {
  return apiRequest<RegularizationRequestOut>(`/attendance/${recordId}/regularization-requests`, {
    method: "POST",
    body,
  });
}

/** `POST /attendance/{id}/regularize` — the HR direct correction, floored at ORG. Audited, no
 *  approval workflow. */
export async function regularizeAttendance(recordId: string, body: RegularizeIn): Promise<AttendanceOut> {
  return apiRequest<AttendanceOut>(`/attendance/${recordId}/regularize`, { method: "POST", body });
}

/* --------------------------------------------------------------------------------- shifts */

export interface ListShiftsParams {
  page?: number | undefined;
  size?: number | undefined;
}

/** `GET /shifts` — `hrms.time.shift.read`, held by both roles. No filter param beyond paging. */
export async function listShifts(params: ListShiftsParams = {}): Promise<Paged<ShiftOut>> {
  const { page = 1, size = 25 } = params;
  if (USE_FIXTURES) {
    const items = __listFixtureShifts();
    const start = (page - 1) * size;
    const slice = items.slice(start, start + size);
    return settle({ items: slice, total: items.length, page, size, pages: Math.max(1, Math.ceil(items.length / size)) });
  }
  return apiRequest<Paged<ShiftOut>>(`/shifts${qs({ page, size })}`);
}

export async function createShift(body: ShiftIn): Promise<ShiftOut> {
  if (USE_FIXTURES) return settle(__createFixtureShift(body));
  return apiRequest<ShiftOut>("/shifts", { method: "POST", body });
}

export async function updateShift(id: string, body: ShiftPatch): Promise<ShiftOut> {
  if (USE_FIXTURES) return settle(__updateFixtureShift(id, body));
  return apiRequest<ShiftOut>(`/shifts/${id}`, { method: "PATCH", body });
}

/** `DELETE /shifts/{id}` — 204, soft-deletes (`deleted_at`); the code stays reserved. */
export async function deleteShift(id: string): Promise<void> {
  if (USE_FIXTURES) {
    __deleteFixtureShift(id);
    await settle(undefined);
    return;
  }
  await apiRequest<void>(`/shifts/${id}`, { method: "DELETE" });
}

/* --------------------------------------------------------------------------------- geofences */

export interface ListGeofencesParams {
  page?: number | undefined;
  size?: number | undefined;
}

/** `GET /geofences` — same permission pair as shifts (`hrms.time.shift.read|write`). */
export async function listGeofences(params: ListGeofencesParams = {}): Promise<Paged<GeofenceOut>> {
  const { page = 1, size = 25 } = params;
  if (USE_FIXTURES) {
    const items = __listFixtureGeofences();
    const start = (page - 1) * size;
    const slice = items.slice(start, start + size);
    return settle({ items: slice, total: items.length, page, size, pages: Math.max(1, Math.ceil(items.length / size)) });
  }
  return apiRequest<Paged<GeofenceOut>>(`/geofences${qs({ page, size })}`);
}

export async function createGeofence(body: GeofenceIn): Promise<GeofenceOut> {
  if (USE_FIXTURES) return settle(__createFixtureGeofence(body));
  return apiRequest<GeofenceOut>("/geofences", { method: "POST", body });
}

export async function updateGeofence(id: string, body: GeofencePatch): Promise<GeofenceOut> {
  if (USE_FIXTURES) return settle(__updateFixtureGeofence(id, body));
  return apiRequest<GeofenceOut>(`/geofences/${id}`, { method: "PATCH", body });
}

export async function deleteGeofence(id: string): Promise<void> {
  if (USE_FIXTURES) {
    __deleteFixtureGeofence(id);
    await settle(undefined);
    return;
  }
  await apiRequest<void>(`/geofences/${id}`, { method: "DELETE" });
}
