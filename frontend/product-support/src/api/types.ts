/**
 * Wire types for the Support product.
 *
 * These are shaped like the responses the support-service will return once it ships
 * (`services/support/app/schemas.py`), so a generated client can drop in later without the UI
 * changing. The backend is a skeleton today, so the app renders from `fixtures.ts`.
 *
 * Times are ISO-8601 UTC strings; SLA state is derived against a fixed "now" so the fixtures render
 * deterministically (see `lib/sla.ts`).
 */

export type Priority = "urgent" | "high" | "normal" | "low";

/** Where a ticket sits in its lifecycle. `solved` is terminal for this slice. */
export type TicketStatus = "new" | "open" | "pending" | "on_hold" | "solved";

/** How the request reached the queue. */
export type Channel = "email" | "chat" | "web" | "phone" | "api";

/** A CSAT rating left by the requester after a ticket is solved. */
export type Satisfaction = "good" | "bad";

/** The person who opened the ticket — a customer contact or an internal staff member. */
export interface Requester {
  name: string;
  email: string;
  company: string;
  /** Distinguishes an external customer from internal staff sharing the one queue. */
  kind: "customer" | "staff";
}

/** One message in a ticket's conversation thread. */
export interface Message {
  id: string;
  author_type: "customer" | "agent" | "system";
  author_name: string;
  at: string;
  body: string;
  /** An internal note is visible to agents only, never the requester. */
  internal?: boolean;
}

/** A support ticket. Mirrors the planned `TicketOut`. */
export interface Ticket {
  id: string;
  subject: string;
  requester: Requester;
  priority: Priority;
  status: TicketStatus;
  channel: Channel;
  /** Agent id, or `null` when the ticket is still unassigned. */
  assignee_id: string | null;
  tags: string[];
  created_at: string;
  updated_at: string;
  /** When the first agent reply went out, or `null` if the clock is still running. */
  first_response_at: string | null;
  /** Deadline for the currently-open SLA clock; `null` once the ticket is solved. */
  sla_due: string | null;
  /** True once an SLA target has been missed (stays true through resolution). */
  sla_breached: boolean;
  /** CSAT left on a solved ticket, or `null`. */
  satisfaction: Satisfaction | null;
  messages: Message[];
}

/** An agent who works the queue. Mirrors the planned `AgentOut`. */
export interface Agent {
  id: string;
  name: string;
  email: string;
  role: "agent" | "senior_agent" | "team_lead";
  team: string;
  /** Currently-assigned open tickets. */
  open_load: number;
  /** Median first-response time over the trailing week, in minutes. */
  avg_first_response_min: number;
  /** Customer-satisfaction score over the trailing week, 0–100. */
  csat_pct: number;
  /** Tickets solved in the trailing seven days. */
  solved_7d: number;
  online: boolean;
}

/** One SLA target row — first response and resolution windows for a priority. */
export interface SlaTarget {
  priority: Priority;
  first_response_min: number;
  resolution_min: number;
}

/** The SLA policy that governs the queue. */
export interface SlaPolicy {
  name: string;
  business_hours: string;
  coverage: string;
  targets: SlaTarget[];
}

/** Small envelope every list endpoint returns. */
export interface Page<T> {
  items: T[];
  total: number;
}

/* --------------------------- overview rollups --------------------------- */

/** One day on the ticket-volume chart. */
export interface VolumePoint {
  /** Short weekday label, e.g. "Mon". */
  label: string;
  /** ISO date for the accessible caption. */
  date: string;
  opened: number;
  solved: number;
}

/** One bar in a categorical breakdown (by priority or by status). */
export interface BreakdownSlice {
  key: string;
  label: string;
  count: number;
  /** Share of the total, 0–100. */
  pct: number;
}
