/**
 * Typed Support API client.
 *
 * Fixtures-first: the support-service is a skeleton on this branch, so the whole app renders from
 * `fixtures.ts` with no login by default. Every function keeps the shape of the live call it will
 * make later, so wiring the real gateway is a flag flip (`VITE_USE_FIXTURES=false`) — the live path
 * targets the same-origin `/api/v1/support/...` prefix that Vite proxies to the gateway in dev.
 */

import { apiRequest } from "./http";
import { AGENTS, SLA_POLICY, TICKETS } from "./fixtures";
import type { Agent, Page, SlaPolicy, Ticket } from "./types";

/** Fixtures unless explicitly turned off — the backend for this product is a skeleton.
 *  Dev builds only: `import.meta.env.DEV` compiles to `false` in production, so a stray
 *  CI/production env var can never ship fixture mode — an app rendering fabricated data
 *  with the "Sample data" markers suppressed. */
export const USE_FIXTURES = import.meta.env.DEV && import.meta.env.VITE_USE_FIXTURES !== "false";

/** True when the app is talking to the real gateway. */
export const LIVE = !USE_FIXTURES;

/** A generous page size — one request returns the whole (small) queue for this org. */
const FULL_PAGE = 100;

/** Small artificial delay so loading states are exercised in fixture mode. */
function settle<T>(value: T, ms = 220): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(value), ms));
}

export interface ListTicketsParams {
  search?: string;
  status?: Ticket["status"] | "all";
  priority?: Ticket["priority"] | "all";
  assigneeId?: string | "all" | "unassigned";
}

function matches(ticket: Ticket, params: ListTicketsParams): boolean {
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

export async function listTickets(params: ListTicketsParams = {}): Promise<Page<Ticket>> {
  if (USE_FIXTURES) {
    const filtered = TICKETS.filter((t) => matches(t, params));
    return settle({ items: filtered, total: filtered.length });
  }

  const query = new URLSearchParams({ size: String(FULL_PAGE) });
  if (params.search?.trim()) query.set("q", params.search.trim());
  if (params.status && params.status !== "all") query.set("status", params.status);
  if (params.priority && params.priority !== "all") query.set("priority", params.priority);
  if (params.assigneeId && params.assigneeId !== "all") query.set("assignee", params.assigneeId);
  return apiRequest<Page<Ticket>>(`/support/tickets?${query.toString()}`);
}

/** The whole queue for this org — powers every overview rollup (KPIs, chart, breakdowns). */
export async function getAllTickets(): Promise<Page<Ticket>> {
  if (USE_FIXTURES) return settle({ items: TICKETS, total: TICKETS.length });
  return apiRequest<Page<Ticket>>(`/support/tickets?size=${FULL_PAGE}`);
}

export async function getTicket(id: string): Promise<Ticket> {
  if (USE_FIXTURES) {
    const found = TICKETS.find((t) => t.id === id);
    if (!found) throw new Error(`Ticket ${id} not found`);
    return settle(found);
  }
  return apiRequest<Ticket>(`/support/tickets/${encodeURIComponent(id)}`);
}

export async function listAgents(): Promise<Agent[]> {
  if (USE_FIXTURES) return settle(AGENTS);
  return apiRequest<Agent[]>(`/support/agents`);
}

export async function getSlaPolicy(): Promise<SlaPolicy> {
  if (USE_FIXTURES) return settle(SLA_POLICY);
  return apiRequest<SlaPolicy>(`/support/sla-policy`);
}
