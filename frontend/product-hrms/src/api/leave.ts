/**
 * Typed client for the leave service (`/api/v1/leave/*`) — live against the gateway, real
 * against a real access token. Mirrors `services/leave/app/api/routes.py` field-for-field, the
 * same "no request shape is invented here that the route does not accept" discipline
 * `api/approvals.ts` states for its own surface.
 *
 * `USE_FIXTURES` renders from `fixtures.ts` for offline design work, exactly the same switch
 * every other live surface in this app uses.
 */

import { apiRequest, buildQuery as qs, settle } from "@opsgrids/client";

import { USE_FIXTURES } from "./client";
import {
  __applyFixtureLeave,
  __cancelFixtureLeave,
  __createFixtureLeaveType,
  __deleteFixtureLeaveType,
  __forceCancelFixtureLeave,
  __grantFixtureLeaveBalance,
  __listFixtureLeaveBalances,
  __listFixtureLeaveLedger,
  __listFixtureLeaveRequests,
  __listFixtureLeaveTypes,
  __updateFixtureLeaveType,
} from "./fixtures";
import type {
  LeaveBalanceGrantIn,
  LeaveBalanceOut,
  LeaveCancelIn,
  LeaveLedgerEntryOut,
  LeaveRequestIn,
  LeaveRequestOut,
  LeaveTypeIn,
  LeaveTypeOut,
  LeaveTypePatch,
  Paged,
} from "./types";


/* --------------------------------------------------------------------------------- leave types */

export interface ListLeaveTypesParams {
  page?: number | undefined;
  size?: number | undefined;
  includeInactive?: boolean | undefined;
}

/** `GET /leave/types` — `hrms.time.leave_type.read`, held by every role at ORG. */
export async function listLeaveTypes(
  params: ListLeaveTypesParams = {},
): Promise<Paged<LeaveTypeOut>> {
  const { page = 1, size = 50, includeInactive = false } = params;
  if (USE_FIXTURES) return settle(__listFixtureLeaveTypes(page, size, includeInactive));
  const query = qs({ page, size, include_inactive: includeInactive ? "true" : undefined });
  return apiRequest<Paged<LeaveTypeOut>>(`/leave/types${query}`);
}

/** `POST /leave/types` — `hrms.time.leave_type.write` at ORG. */
export async function createLeaveType(body: LeaveTypeIn): Promise<LeaveTypeOut> {
  if (USE_FIXTURES) return settle(__createFixtureLeaveType(body));
  return apiRequest<LeaveTypeOut>("/leave/types", { method: "POST", body });
}

/** `PATCH /leave/types/{id}` — only the keys present are touched. */
export async function updateLeaveType(id: string, body: LeaveTypePatch): Promise<LeaveTypeOut> {
  if (USE_FIXTURES) return settle(__updateFixtureLeaveType(id, body));
  return apiRequest<LeaveTypeOut>(`/leave/types/${id}`, { method: "PATCH", body });
}

/** `DELETE /leave/types/{id}` — 204, soft-deletes (`deleted_at`); the code stays reserved. */
export async function deleteLeaveType(id: string): Promise<void> {
  if (USE_FIXTURES) {
    __deleteFixtureLeaveType(id);
    await settle(undefined);
    return;
  }
  await apiRequest<void>(`/leave/types/${id}`, { method: "DELETE" });
}

/* --------------------------------------------------------------------------------- balances */

/** `GET /leave/balances?employee_id=` — required: this endpoint has no "everyone" read (no page
 *  parameter would bound it — see `services/leave/app/api/routes.py::list_leave_balances`). */
export async function listLeaveBalances(employeeId: string): Promise<LeaveBalanceOut[]> {
  if (USE_FIXTURES) return settle(__listFixtureLeaveBalances(employeeId));
  return apiRequest<LeaveBalanceOut[]>(
    `/leave/balances${qs({ employee_id: employeeId })}`,
  );
}

/** `GET /leave/balances/ledger?employee_id=&leave_type_id=` — one employee's full posting
 *  history for one leave type, oldest first. */
export async function listLeaveLedger(
  employeeId: string,
  leaveTypeId: string,
): Promise<LeaveLedgerEntryOut[]> {
  if (USE_FIXTURES) return settle(__listFixtureLeaveLedger(employeeId, leaveTypeId));
  return apiRequest<LeaveLedgerEntryOut[]>(
    `/leave/balances/ledger${qs({ employee_id: employeeId, leave_type_id: leaveTypeId })}`,
  );
}

/** `POST /leave/balances/grant` — `hrms.time.leave_balance.write` at ORG. Idempotent on
 *  `idempotency_key` when the caller supplies one. */
export async function grantLeaveBalance(body: LeaveBalanceGrantIn): Promise<LeaveLedgerEntryOut> {
  if (USE_FIXTURES) return settle(__grantFixtureLeaveBalance(body));
  return apiRequest<LeaveLedgerEntryOut>("/leave/balances/grant", { method: "POST", body });
}

/* --------------------------------------------------------------------------------- requests */

export interface ListLeaveRequestsParams {
  employeeId?: string | undefined;
  status?: LeaveRequestOut["status"] | undefined;
  page?: number | undefined;
  size?: number | undefined;
}

/** `GET /leave/requests` — scope-filtered server-side; `employeeId` narrows further, the same
 *  "layered on top of scope, never a replacement for it" rule `api/approvals.ts` states for its
 *  own `requester=me`. */
export async function listLeaveRequests(
  params: ListLeaveRequestsParams = {},
): Promise<Paged<LeaveRequestOut>> {
  const { employeeId, status, page = 1, size = 25 } = params;
  if (USE_FIXTURES) return settle(__listFixtureLeaveRequests(employeeId, status, page, size));
  const query = qs({ employee_id: employeeId, status, page, size });
  return apiRequest<Paged<LeaveRequestOut>>(`/leave/requests${query}`);
}

/** `POST /leave/requests` — self-service; there is no `employee_id` in the body, the caller
 *  applies for their own leave. Routes through approvals; the decision lands via the approval
 *  consumer, the same round trip attendance's regularisation request uses. */
export async function applyForLeave(body: LeaveRequestIn): Promise<LeaveRequestOut> {
  if (USE_FIXTURES) return settle(__applyFixtureLeave(body));
  return apiRequest<LeaveRequestOut>("/leave/requests", { method: "POST", body });
}

/** `POST /leave/requests/{id}/cancel` — withdraw your own pending or approved request. */
export async function cancelLeaveRequest(
  requestId: string,
  body: LeaveCancelIn = {},
): Promise<LeaveRequestOut> {
  if (USE_FIXTURES) return settle(__cancelFixtureLeave(requestId, body));
  return apiRequest<LeaveRequestOut>(`/leave/requests/${requestId}/cancel`, {
    method: "POST",
    body,
  });
}

/** `POST /leave/requests/{id}/force-cancel` — the HR override, `hrms.time.leave_request.manage`
 *  at ORG. Cancels any employee's request directly, without going through approval. */
export async function forceCancelLeaveRequest(
  requestId: string,
  body: LeaveCancelIn = {},
): Promise<LeaveRequestOut> {
  if (USE_FIXTURES) return settle(__forceCancelFixtureLeave(requestId, body));
  return apiRequest<LeaveRequestOut>(`/leave/requests/${requestId}/force-cancel`, {
    method: "POST",
    body,
  });
}
