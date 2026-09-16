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
  __createFixtureDelegation,
  __listFixturePolicies,
  __listFixtureDelegations,
  __revokeFixtureDelegation,
  __searchFixtureDelegateCandidates,
  __updateFixturePolicy,
} from "./fixtures";
import { USE_FIXTURES } from "./client";
import type {
  ApprovalChainOut,
  DecisionIn,
  DelegateCandidateOut,
  DelegationCreateIn,
  DelegationCreateOut,
  DelegationOut,
  InboxItemOut,
  Paged,
  PolicyOut,
  PolicyUpdateIn,
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

/* --------------------------------------------------------------------- delegation (slice 3) */

/**
 * `GET /approvals/delegate-candidates?q=` — the delegate picker's source (§4.E). `q` is required
 * and at least two characters; the `Combobox` calling this never sends fewer (§4.E's "a 422 for a
 * state the UI can prevent is a bug"). Results are already capped at 20 by the server.
 *
 * `signal` is accepted for `Combobox`'s own abort-on-keystroke contract, but `@opsgrids/client`'s
 * `apiRequest` takes no `AbortSignal` today (`RequestOptions` has no such field) — a client gap
 * outside this package's ownership, noted in the slice-3 report. `Combobox` stays correct
 * regardless: it discards any response that resolves after a newer query superseded it by
 * checking `signal.aborted` itself, so an unlinked signal costs an occasional wasted request, not
 * a wrong one.
 */
export async function listDelegateCandidates(
  q: string,
  signal?: AbortSignal,
): Promise<DelegateCandidateOut[]> {
  if (USE_FIXTURES) return settle(__searchFixtureDelegateCandidates(q, 20));
  void signal;
  return apiRequest<DelegateCandidateOut[]>(
    `/approvals/delegate-candidates?${new URLSearchParams({ q }).toString()}`,
  );
}

export interface DelegationListParams extends PageParams {
  /**
   * `employee_id` — narrows to delegations this person is a party to, in **either** direction
   * (delegator or delegate), ANDed with the caller's own scope. The server filters; the screen
   * does not, which is what keeps `total` and "Load more" telling the truth about the same set.
   */
  employeeId?: string;
}

/**
 * `GET /approvals/delegations` — ORG sees every delegation in the organization; below ORG sees
 * only rows the caller is a party to, delegator or delegate, newest first. `employee_id` narrows
 * that set server-side (§4.E's "filtered to a person"); omitting it is "everything my scope
 * reaches", never "everything".
 */
export async function listDelegations(
  params: DelegationListParams = {},
): Promise<Paged<DelegationOut>> {
  const { employeeId, ...page } = params;
  if (USE_FIXTURES) {
    // `|| null`, not `?? null`: an empty string is "no filter" on the live path below, and the
    // two must not disagree about what an absent value looks like.
    return settle(paginate(__listFixtureDelegations(employeeId || null), page));
  }
  const query = pageQuery(page);
  const filter = employeeId
    ? `&${new URLSearchParams({ employee_id: employeeId }).toString()}`
    : "";
  return apiRequest<Paged<DelegationOut>>(`/approvals/delegations?${query}${filter}`);
}

/**
 * `POST /approvals/delegations` — omit `delegator_employee_id` to create for yourself; supplying
 * a different id needs `delegation.write:ORG` and is refused with 403 below that (§4.E's "for
 * another employee" mode). Returns the delegation plus `repointed_count`. Refusals: 422 with
 * `errors[{field,code,message}]` for self-delegation, an end before or equal to the start, an end
 * in the past, or a delegate who has left; 409 on an overlapping active delegation, naming the
 * conflicting range in `problem.detail` when the opportunistic check catches it.
 */
export async function createDelegation(body: DelegationCreateIn): Promise<DelegationCreateOut> {
  if (USE_FIXTURES) {
    const { delegation, repointed_count } = __createFixtureDelegation(body);
    return settle({ ...delegation, repointed_count });
  }
  return apiRequest<DelegationCreateOut>("/approvals/delegations", { method: "POST", body });
}

/**
 * `POST /approvals/delegations/{id}/revoke` — returns the revoked row. Below ORG only the
 * delegator may revoke: a bystander (neither party) gets 404, the delegate gets 403. A second
 * revoke is 409.
 */
export async function revokeDelegation(delegationId: string): Promise<DelegationOut> {
  if (USE_FIXTURES) return settle(__revokeFixtureDelegation(delegationId));
  return apiRequest<DelegationOut>(`/approvals/delegations/${delegationId}/revoke`, {
    method: "POST",
  });
}

/* ----------------------------------------------------------- policy administration (slice 4) */

/**
 * `GET /approvals/policies` — one row per request type approval knows about (today, exactly one:
 * `hrms.time.regularization`), readable by anyone who can read requests at all, at any scope —
 * the one read endpoint whose scope is not consulted (screens §4.G, §5 row 15). A type with no
 * configured row still comes back, at the server's own defaults, so the settings screen never
 * fabricates a row itself.
 */
export async function listPolicies(): Promise<PolicyOut[]> {
  if (USE_FIXTURES) return settle(__listFixturePolicies());
  return apiRequest<PolicyOut[]>("/approvals/policies");
}

/**
 * `PUT /approvals/policies/{request_type}` — `policy.write`, floored at ORG. Refused with a
 * field-level 422 on `sla_minutes` outside `15..525600` or on an unrecognised `request_type`,
 * verbatim from the server (§2.5) — this function never narrows or renames the bound itself.
 */
export async function updatePolicy(
  requestType: string,
  body: PolicyUpdateIn,
): Promise<PolicyOut> {
  if (USE_FIXTURES) return settle(__updateFixturePolicy(requestType, body));
  return apiRequest<PolicyOut>(`/approvals/policies/${encodeURIComponent(requestType)}`, {
    method: "PUT",
    body,
  });
}
