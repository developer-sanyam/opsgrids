/**
 * Typed client for approval's slice-2 surface (`/api/v1/approvals/*`) — live against the
 * gateway, real against a real access token. `services/approval` and its permission catalogue
 * both ship this slice (screens spec §6), so unlike `listAttendance` this module has no
 * long-running sample mode: `USE_FIXTURES` renders from `fixtures.ts` for offline design work,
 * exactly the same switch every other live surface in this app uses.
 *
 * Every function mirrors `services/approval/app/api/routes.py` field-for-field — no request shape
 * is invented here that the route does not accept, and no response field is read that the route
 * does not return.
 */

import { ApiError, apiRequest, settle } from "@opsgrids/client";

import {
  FIXTURE_CHAIN,
  FIXTURE_DETAIL_BY_ID,
  FIXTURE_INBOX,
  FIXTURE_MY_REQUESTS,
} from "./fixtures";
import { USE_FIXTURES } from "./client";
import type {
  ApprovalChainOut,
  DecisionIn,
  InboxItemOut,
  Paged,
  RequestDetailOut,
  RequestListItemOut,
} from "./types";

export interface PageParams {
  page?: number;
  size?: number;
}

function pageQuery({ page = 1, size = 25 }: PageParams): string {
  return new URLSearchParams({ page: String(page), size: String(size) }).toString();
}

function paginate<T>(items: T[], { page = 1, size = 25 }: PageParams): Paged<T> {
  const start = (page - 1) * size;
  return {
    items: items.slice(start, start + size),
    total: items.length,
    page,
    size,
    pages: Math.max(1, Math.ceil(items.length / size)),
  };
}

/**
 * `GET /approvals/inbox` — pending requests the caller may decide. The backend accepts only
 * `page`/`size` today (no search, filter or sort query params): §4.A's search box and filter
 * pills are applied client-side, over the pages already loaded, and are a known gap against the
 * screens spec — see the slice-2 report.
 */
export async function listInbox(params: PageParams = {}): Promise<Paged<InboxItemOut>> {
  if (USE_FIXTURES) return settle(paginate(FIXTURE_INBOX, params));
  return apiRequest<Paged<InboxItemOut>>(`/approvals/inbox?${pageQuery(params)}`);
}

/**
 * `GET /approvals/requests?requester=me` — "My requests" (§4.D). `requester=me` is layered on
 * top of scope, not a replacement for it (`services/approval/app/api/routes.py`): without it a
 * TEAM, LOCATION or ORG-scoped caller would see every request their read grant reaches on a
 * screen titled "My requests" — for an HR admin with `read:ORG`, the whole organization's. The
 * server resolves "me" from the caller's own subject; there is no `requester=<uuid>` shape to
 * send and nothing here ever tries to.
 */
export async function listMyRequests(params: PageParams = {}): Promise<Paged<RequestListItemOut>> {
  if (USE_FIXTURES) return settle(paginate(FIXTURE_MY_REQUESTS, params));
  return apiRequest<Paged<RequestListItemOut>>(
    `/approvals/requests?${pageQuery(params)}&requester=me`,
  );
}

/** `GET /approvals/requests/{id}` — readable by the requester, anyone on a step (any outcome),
 *  or an ORG reader. An id the caller may not read is a 404, never a 403 (ADR-0006 §11). */
export async function getRequestDetail(requestId: string): Promise<RequestDetailOut> {
  if (USE_FIXTURES) {
    const detail = FIXTURE_DETAIL_BY_ID[requestId];
    if (!detail) {
      throw new ApiError(404, "That request isn't available to you", {
        detail: "It may have been removed, or it may belong to someone else's chain.",
      });
    }
    return settle(detail);
  }
  return apiRequest<RequestDetailOut>(`/approvals/requests/${requestId}`);
}

/**
 * `POST /approvals/requests/{id}/decision` — 403 not eligible, 409 already decided (with the
 * recorded outcome only in the message; the decider's name and instant come from the refetch).
 */
export async function decideRequest(
  requestId: string,
  body: DecisionIn,
): Promise<RequestDetailOut> {
  if (USE_FIXTURES) {
    const detail = FIXTURE_DETAIL_BY_ID[requestId];
    if (!detail) {
      throw new ApiError(404, "That request isn't available to you", {
        detail: "It may have been removed, or it may belong to someone else's chain.",
      });
    }
    if (detail.status !== "pending") {
      throw new ApiError(409, `This request was already ${detail.status}`, {
        detail: `This request was already ${detail.status}`,
      });
    }
    const decidedAt = new Date().toISOString();
    const outcome = body.outcome === "approve" ? "approved" : "rejected";
    const decided: RequestDetailOut = {
      ...detail,
      status: outcome,
      decided_by_employee_id: "fixture-decider",
      decided_by_name: "You",
      decided_at: decidedAt,
      comment: body.comment ?? null,
      inbox_reason: null,
      application_state: "applied",
      application_failure_reason: null,
      applied_at: decidedAt,
      steps: detail.steps.map((step, i) =>
        i === detail.steps.length - 1
          ? { ...step, outcome, closed_at: decidedAt }
          : step,
      ),
    };
    FIXTURE_DETAIL_BY_ID[requestId] = decided;
    return settle(decided);
  }
  return apiRequest<RequestDetailOut>(`/approvals/requests/${requestId}/decision`, {
    method: "POST",
    body,
  });
}

/** `GET /approvals/chain/{employee_id}` — the resolved approval chain for one employee. */
export async function getChain(employeeId: string): Promise<ApprovalChainOut> {
  if (USE_FIXTURES) return settle({ ...FIXTURE_CHAIN, employee_id: employeeId });
  return apiRequest<ApprovalChainOut>(`/approvals/chain/${employeeId}`);
}
