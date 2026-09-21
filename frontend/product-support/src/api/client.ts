/**
 * Typed Support API client.
 *
 * Live by default: every function calls the support-service through the gateway at the
 * same-origin `/api/v1/support/...` prefix (Vite proxies it to the gateway in dev) with the
 * bearer token from the session. Set `VITE_USE_FIXTURES=true` (dev only — `import.meta.env.DEV`
 * compiles to `false` in a production build) to render the whole app from `fixtures.ts` with no
 * login, for offline design work.
 *
 * Every function mirrors `services/support/app/api/routes.py` and `app/schemas.py`
 * field-for-field: no request shape is invented here that the route does not accept, and no
 * response field is read that the route does not return.
 */

import { ApiError, apiRequest, settle } from "@opsgrids/client";

import { buildFixtureOverview } from "./fixtureOverview";
import {
  __assignFixtureTicket,
  __changeFixtureTicketStatus,
  __createFixtureAgent,
  __createFixtureTicket,
  __deleteFixtureAgent,
  __getFixtureTicket,
  __replyToFixtureTicket,
  __setFixtureSlaPolicy,
  __updateFixtureAgent,
  __updateFixtureTicket,
  AGENTS,
  SLA_POLICY,
  TICKETS,
} from "./fixtures";
import type {
  Agent,
  AgentIn,
  AgentPatch,
  Overview,
  Page,
  Priority,
  ReplyIn,
  SlaPolicy,
  SlaPolicyIn,
  Ticket,
  TicketCreateIn,
  TicketStatus,
  TicketUpdateIn,
} from "./types";

/** Default source. Live unless `VITE_USE_FIXTURES=true` pins the app to fixtures.
 *  Dev builds only: `import.meta.env.DEV` compiles to `false` in production, so a stray
 *  CI/production env var can never ship fixture mode — an app rendering fabricated data
 *  with the "Sample data" markers suppressed. */
export const USE_FIXTURES = import.meta.env.DEV && import.meta.env.VITE_USE_FIXTURES === "true";

/** True when the app is talking to the real gateway. */
export const LIVE = !USE_FIXTURES;

/**
 * The Overview's KPI headline numbers, volume chart and both breakdowns all come from
 * `GET /support/overview` now. What has no reporting service behind it at all is the
 * week-over-week delta and sparkline beside each KPI — there is no history endpoint to draw
 * them from, live or not — so those two stay representative and carry `SampleChip`.
 */
export const TRENDS_ARE_SAMPLE = true;

/** The sort keys the queue's `GET /support/tickets` accepts (`selectors.py: SORTS`). Each key
 *  fixes its own direction server-side — there is no separate `dir` parameter. */
export type TicketSort = "number" | "created" | "updated" | "priority";

const PRIORITY_RANK: Record<Priority, number> = { urgent: 0, high: 1, normal: 2, low: 3 };

function sortFixtureTickets(rows: Ticket[], sort: TicketSort): Ticket[] {
  const out = [...rows];
  if (sort === "created") return out.sort((a, b) => b.created_at.localeCompare(a.created_at));
  if (sort === "updated") return out.sort((a, b) => b.updated_at.localeCompare(a.updated_at));
  if (sort === "priority") {
    return out.sort(
      (a, b) =>
        PRIORITY_RANK[a.priority] - PRIORITY_RANK[b.priority] ||
        b.created_at.localeCompare(a.created_at),
    );
  }
  return out.sort((a, b) => a.id.localeCompare(b.id));
}

function paginate<T>(items: T[], page: number, size: number): Page<T> {
  const start = (page - 1) * size;
  const total = items.length;
  return {
    items: items.slice(start, start + size),
    total,
    page,
    size,
    pages: total === 0 ? 0 : Math.max(1, Math.ceil(total / size)),
  };
}

export interface ListTicketsParams {
  search?: string;
  status?: TicketStatus | "all";
  priority?: Priority | "all";
  /** Agent id, `"unassigned"`, or `"all"` for no filter. */
  assigneeId?: string | "all" | "unassigned";
  sort?: TicketSort;
  page?: number;
  size?: number;
}

function matchesFixture(ticket: Ticket, params: ListTicketsParams): boolean {
  const q = params.search?.trim().toLowerCase() ?? "";
  const matchesQuery =
    q === "" ||
    ticket.subject.toLowerCase().includes(q) ||
    ticket.id.toLowerCase().includes(q) ||
    ticket.requester.name.toLowerCase().includes(q) ||
    ticket.requester.company.toLowerCase().includes(q);
  const matchesStatus =
    !params.status || params.status === "all" || ticket.status === params.status;
  const matchesPriority =
    !params.priority || params.priority === "all" || ticket.priority === params.priority;
  const matchesAssignee =
    !params.assigneeId ||
    params.assigneeId === "all" ||
    (params.assigneeId === "unassigned"
      ? ticket.assignee_id === null
      : ticket.assignee_id === params.assigneeId);
  return matchesQuery && matchesStatus && matchesPriority && matchesAssignee;
}

/** `GET /support/tickets` — the queue, server-paged and server-sorted. */
export async function listTickets(params: ListTicketsParams = {}): Promise<Page<Ticket>> {
  const page = params.page ?? 1;
  const size = params.size ?? 25;
  const sort = params.sort ?? "number";
  if (USE_FIXTURES) {
    const filtered = sortFixtureTickets(TICKETS.filter((t) => matchesFixture(t, params)), sort);
    return settle(paginate(filtered, page, size));
  }

  const query = new URLSearchParams({ page: String(page), size: String(size), sort });
  if (params.search?.trim()) query.set("q", params.search.trim());
  if (params.status && params.status !== "all") query.set("status", params.status);
  if (params.priority && params.priority !== "all") query.set("priority", params.priority);
  if (params.assigneeId && params.assigneeId !== "all") query.set("assignee", params.assigneeId);
  return apiRequest<Page<Ticket>>(`/support/tickets?${query.toString()}`);
}

export async function getTicket(number: string): Promise<Ticket> {
  if (USE_FIXTURES) return settle(__getFixtureTicket(number));
  return apiRequest<Ticket>(`/support/tickets/${encodeURIComponent(number)}`);
}

/** `POST /support/tickets`. Server-owned: `number`, `status`, timestamps, SLA fields. */
export async function createTicket(payload: TicketCreateIn): Promise<Ticket> {
  if (USE_FIXTURES) return settle(__createFixtureTicket(payload));
  return apiRequest<Ticket>("/support/tickets", { method: "POST", body: payload });
}

/** `PATCH /support/tickets/{id}` — priority, subject and/or tags. Status and assignee stay on
 *  their own endpoints below. */
export async function updateTicket(number: string, payload: TicketUpdateIn): Promise<Ticket> {
  if (USE_FIXTURES) return settle(__updateFixtureTicket(number, payload));
  return apiRequest<Ticket>(`/support/tickets/${encodeURIComponent(number)}`, {
    method: "PATCH",
    body: payload,
  });
}

/** `POST /support/tickets/{id}/status` — a legal lifecycle move, or a 409 with the server's
 *  explanation. */
export async function changeTicketStatus(number: string, status: TicketStatus): Promise<Ticket> {
  if (USE_FIXTURES) return settle(__changeFixtureTicketStatus(number, status));
  return apiRequest<Ticket>(`/support/tickets/${encodeURIComponent(number)}/status`, {
    method: "POST",
    body: { status },
  });
}

/** `POST /support/tickets/{id}/reply` — appends to the thread. `author_type` is never sent: the
 *  server fixes every reply through this endpoint to `agent`. */
export async function replyToTicket(number: string, payload: ReplyIn): Promise<Ticket> {
  if (USE_FIXTURES) {
    return settle(
      __replyToFixtureTicket(number, payload.body, payload.author_name, payload.internal ?? false),
    );
  }
  return apiRequest<Ticket>(`/support/tickets/${encodeURIComponent(number)}/reply`, {
    method: "POST",
    body: payload,
  });
}

/** `POST /support/tickets/{id}/assign` — `assigneeId: null` unassigns. */
export async function assignTicket(number: string, assigneeId: string | null): Promise<Ticket> {
  if (USE_FIXTURES) return settle(__assignFixtureTicket(number, assigneeId));
  return apiRequest<Ticket>(`/support/tickets/${encodeURIComponent(number)}/assign`, {
    method: "POST",
    body: { assignee_id: assigneeId },
  });
}

/** `GET /support/agents` — no paging or search; the desk is small enough for one page. */
export async function listAgents(): Promise<Agent[]> {
  if (USE_FIXTURES) return settle([...AGENTS]);
  return apiRequest<Agent[]>("/support/agents");
}

/** `POST /support/agents`. `ref` is server-allocated. Permission `support.desk.agent.write`. */
export async function createAgent(payload: AgentIn): Promise<Agent> {
  if (USE_FIXTURES) return settle(__createFixtureAgent(payload));
  return apiRequest<Agent>("/support/agents", { method: "POST", body: payload });
}

/** `PATCH /support/agents/{ref}`. */
export async function updateAgent(ref: string, payload: AgentPatch): Promise<Agent> {
  if (USE_FIXTURES) return settle(__updateFixtureAgent(ref, payload));
  return apiRequest<Agent>(`/support/agents/${encodeURIComponent(ref)}`, {
    method: "PATCH",
    body: payload,
  });
}

/** `DELETE /support/agents/{ref}` — soft-deletes the agent and unassigns their open tickets. */
export async function deleteAgent(ref: string): Promise<void> {
  if (USE_FIXTURES) {
    __deleteFixtureAgent(ref);
    return settle(undefined);
  }
  await apiRequest<void>(`/support/agents/${encodeURIComponent(ref)}`, { method: "DELETE" });
}

/** `GET /support/sla-policy` — 404s when the organization has none. Render that as "no policy
 *  yet, set one up", never as a generic error. */
export async function getSlaPolicy(): Promise<SlaPolicy> {
  if (USE_FIXTURES) {
    if (SLA_POLICY === null) {
      throw new ApiError(404, "No SLA policy is configured for this organization", {
        title: "Not Found",
        status: 404,
        detail: "No SLA policy is configured for this organization",
      });
    }
    return settle(SLA_POLICY);
  }
  return apiRequest<SlaPolicy>("/support/sla-policy");
}

/** `PUT /support/sla-policy`. Permission `support.desk.sla_policy.write`. */
export async function setSlaPolicy(payload: SlaPolicyIn): Promise<SlaPolicy> {
  if (USE_FIXTURES) return settle(__setFixtureSlaPolicy(payload));
  return apiRequest<SlaPolicy>("/support/sla-policy", { method: "PUT", body: payload });
}

/** `GET /support/overview` — every KPI, the volume series and both breakdowns, computed
 *  server-side over the whole organization, not a capped page of tickets. */
export async function getOverview(): Promise<Overview> {
  if (USE_FIXTURES) return settle(buildFixtureOverview(TICKETS));
  return apiRequest<Overview>("/support/overview");
}
