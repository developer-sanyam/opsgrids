/**
 * Every write function asserted against a stubbed `fetch`: exact method, URL and JSON body — the
 * same discipline `@opsgrids/client`'s own `http.test.ts` holds every request to. `USE_FIXTURES`
 * is false in this environment (no `VITE_USE_FIXTURES` set), so every call below goes to
 * `apiRequest` and therefore to `fetch`.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  checkIn,
  checkOut,
  createEmployee,
  createGeofence,
  createShift,
  deleteGeofence,
  deleteShift,
  listAttendance,
  listEmployees,
  regularizeAttendance,
  requestRegularization,
  transitionEmployee,
  unmaskEmployeePii,
  updateEmployee,
  updateShift,
} from "./client";

function respond(status: number, body: unknown) {
  return new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function stubFetch(status: number, body: unknown) {
  const fetchMock = vi.fn().mockResolvedValue(respond(status, body));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("employee writes", () => {
  it("createEmployee — POST /employees with the exact body, never membership_id", async () => {
    const fetchMock = stubFetch(201, { id: "emp-1" });
    await createEmployee({ employee_no: "ATM-0900", full_name: "New Person" });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/employees");
    expect(init.method).toBe("POST");
    const body = JSON.parse(init.body as string) as Record<string, unknown>;
    expect(body).toEqual({ employee_no: "ATM-0900", full_name: "New Person" });
    expect(body.membership_id).toBeUndefined();
  });

  it("updateEmployee — PATCH /employees/{id} with only the given keys", async () => {
    const fetchMock = stubFetch(200, { id: "emp-1" });
    await updateEmployee("emp-1", { department_id: null });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/employees/emp-1");
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(init.body as string)).toEqual({ department_id: null });
  });

  it("transitionEmployee — POST /employees/{id}/transition", async () => {
    const fetchMock = stubFetch(200, { id: "emp-1", status: "exited" });
    await transitionEmployee("emp-1", { to: "exited", reason: "Resigned" });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/employees/emp-1/transition");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({ to: "exited", reason: "Resigned" });
  });

  it("unmaskEmployeePii — POST /employees/{id}/pii/unmask", async () => {
    const fetchMock = stubFetch(200, { personal_email: "a@b.com" });
    await unmaskEmployeePii("emp-1", { fields: ["personal_email"] });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/employees/emp-1/pii/unmask");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({ fields: ["personal_email"] });
  });

  it("listEmployees — GET /employees with q, department_id, status and paging in the query", async () => {
    const fetchMock = stubFetch(200, { items: [], total: 0, page: 1, size: 25, pages: 0 });
    await listEmployees({ q: "ana", departmentId: "dept-1", status: "active", page: 2, size: 50 });
    const [url] = fetchMock.mock.calls[0] as [string];
    expect(url).toBe("/api/v1/employees?q=ana&department_id=dept-1&status=active&page=2&size=50");
  });

  it("listEmployees — omits status when 'all'", async () => {
    const fetchMock = stubFetch(200, { items: [], total: 0, page: 1, size: 25, pages: 0 });
    await listEmployees({ status: "all" });
    const [url] = fetchMock.mock.calls[0] as [string];
    expect(url).toBe("/api/v1/employees?page=1&size=25");
  });
});

describe("attendance writes", () => {
  it("checkIn — POST /attendance/check-in with no employee_id in the body", async () => {
    const fetchMock = stubFetch(201, { id: "att-1" });
    await checkIn({ work_mode: "field", source: "web", lat: 12.9, lng: 77.5 });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/attendance/check-in");
    expect(init.method).toBe("POST");
    const body = JSON.parse(init.body as string) as Record<string, unknown>;
    expect(body).toEqual({ work_mode: "field", source: "web", lat: 12.9, lng: 77.5 });
    expect(body.employee_id).toBeUndefined();
  });

  it("checkOut — POST /attendance/check-out", async () => {
    const fetchMock = stubFetch(200, { id: "att-1" });
    await checkOut({ lat: 12.9, lng: 77.5 });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/attendance/check-out");
    expect(init.method).toBe("POST");
  });

  it("requestRegularization — POST /attendance/{id}/regularization-requests", async () => {
    const fetchMock = stubFetch(201, { id: "reg-1" });
    await requestRegularization("att-1", { requested_status: "present", reason: "Forgot to punch" });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/attendance/att-1/regularization-requests");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({
      requested_status: "present",
      reason: "Forgot to punch",
    });
  });

  it("regularizeAttendance — POST /attendance/{id}/regularize, the HR direct correction", async () => {
    const fetchMock = stubFetch(200, { id: "att-1", status: "present" });
    await regularizeAttendance("att-1", { to: "present", reason: "HR override" });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/attendance/att-1/regularize");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({ to: "present", reason: "HR override" });
  });

  it("listAttendance — GET /attendance with date_from/date_to/employee_id/status/paging", async () => {
    const fetchMock = stubFetch(200, { items: [], total: 0, page: 1, size: 25, pages: 0 });
    await listAttendance({ employeeId: "emp-1", dateFrom: "2026-09-01", dateTo: "2026-09-01" });
    const [url] = fetchMock.mock.calls[0] as [string];
    expect(url).toBe("/api/v1/attendance?employee_id=emp-1&date_from=2026-09-01&date_to=2026-09-01&page=1&size=25");
  });
});

describe("shifts & geofences writes", () => {
  it("createShift — POST /shifts", async () => {
    const fetchMock = stubFetch(201, { id: "shift-1" });
    await createShift({ code: "GEN", name: "General", start_time: "09:00", end_time: "18:00" });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/shifts");
    expect(init.method).toBe("POST");
  });

  it("updateShift — PATCH /shifts/{id} without `code`", async () => {
    const fetchMock = stubFetch(200, { id: "shift-1" });
    await updateShift("shift-1", { name: "Renamed" });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/shifts/shift-1");
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(init.body as string)).toEqual({ name: "Renamed" });
  });

  it("deleteShift — DELETE /shifts/{id}", async () => {
    const fetchMock = stubFetch(204, undefined);
    await deleteShift("shift-1");
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/shifts/shift-1");
    expect(init.method).toBe("DELETE");
  });

  it("createGeofence — POST /geofences, coordinates sent as strings", async () => {
    const fetchMock = stubFetch(201, { id: "geo-1" });
    await createGeofence({
      location_code: "Bengaluru",
      name: "Campus",
      center_lat: "12.9716",
      center_lng: "77.5946",
      radius_m: 200,
    });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/geofences");
    const body = JSON.parse(init.body as string) as Record<string, unknown>;
    expect(body.center_lat).toBe("12.9716");
    expect(typeof body.center_lat).toBe("string");
  });

  it("deleteGeofence — DELETE /geofences/{id}", async () => {
    const fetchMock = stubFetch(204, undefined);
    await deleteGeofence("geo-1");
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/geofences/geo-1");
    expect(init.method).toBe("DELETE");
  });
});
