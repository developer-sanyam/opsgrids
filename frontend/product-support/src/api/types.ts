/**
 * Wire types for the Support product — mirrored field-for-field against
 * `services/support/app/schemas.py`, which is the published contract. `id` is the human code
 * (`TK-4001`, `ag-01`); times are ISO-8601 UTC strings.
 */

export type Priority = "urgent" | "high" | "normal" | "low";

/** Where a ticket sits in its lifecycle. `solved` reopens to `open`, nowhere else. */
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

/** One message in a ticket's conversation thread — append-only, never edited or deleted. */
export interface Message {
  id: string;
  author_type: "customer" | "agent" | "system";
  author_name: string;
  at: string;
  body: string;
  /** An internal note is visible to agents only, never the requester. */
  internal?: boolean;
}

/** A support ticket. Mirrors `TicketOut`. */
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

/** An agent who works the queue. Mirrors `AgentOut`. */
export interface Agent {
  id: string;
  name: string;
  email: string;
  role: "agent" | "senior_agent" | "team_lead";
  team: string;
  /** Currently-assigned open tickets — a desk projection, never client-writable. */
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

/** The SLA policy that governs the queue. Exactly one per organization. */
export interface SlaPolicy {
  name: string;
  business_hours: string;
  coverage: string;
  targets: SlaTarget[];
}

/** The envelope every list endpoint on this platform returns. */
export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  size: number;
  pages: number;
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

/** `GET /support/overview` — the queue's KPIs, volume and breakdowns, computed server-side over
 *  every ticket in the organization, not a page of them. */
export interface Overview {
  open_tickets: number;
  breaching_sla: number;
  first_response_median_min: number;
  csat_pct: number | null;
  volume: VolumePoint[];
  priority_breakdown: BreakdownSlice[];
  status_breakdown: BreakdownSlice[];
}

/* --------------------------------------------------------------------- write contracts
 * One interface per Pydantic request model (`services/support/app/schemas.py`) — the exact
 * shape the wire accepts, so a field this module does not send is a field the client never
 * invented.
 */

/** `POST /support/tickets/{id}/reply`. `author_type` is not a field: every reply through this
 *  endpoint is fixed to `agent` server-side. */
export interface ReplyIn {
  body: string;
  author_name: string;
  internal?: boolean;
}

/** `POST /support/tickets/{id}/assign`. `null` unassigns. */
export interface AssignIn {
  assignee_id: string | null;
}

/** `POST /support/tickets/{id}/status`. The target must be a legal next state — otherwise 409. */
export interface StatusIn {
  status: TicketStatus;
}

/** The requester snapshot taken when a ticket opens. */
export interface RequesterIn {
  name: string;
  email: string;
  company: string;
  kind?: "customer" | "staff";
}

/** `POST /support/tickets`. Server-owned and never sent here: `number`, `status`, `created_at`,
 *  `first_response_at`, `sla_due`, `sla_breached`, `satisfaction`. */
export interface TicketCreateIn {
  subject: string;
  requester: RequesterIn;
  priority?: Priority;
  channel?: Channel;
  assignee_id?: string | null;
  tags?: string[];
  body: string;
}

/** `PATCH /support/tickets/{id}`. Absent means "leave alone"; at least one field is required. */
export interface TicketUpdateIn {
  priority?: Priority;
  subject?: string;
  tags?: string[];
}

/** `POST /support/agents`. `ref` is server-allocated. */
export interface AgentIn {
  name: string;
  email: string;
  role?: Agent["role"];
  team?: string;
  online?: boolean;
}

/** `PATCH /support/agents/{ref}`. Absent means "leave alone"; at least one field is required. */
export interface AgentPatch {
  name?: string;
  email?: string;
  role?: Agent["role"];
  team?: string;
  online?: boolean;
}

export interface SlaTargetIn {
  priority: Priority;
  first_response_min: number;
  resolution_min: number;
}

/** `PUT /support/sla-policy`. `targets` must carry exactly one row per priority. */
export interface SlaPolicyIn {
  name: string;
  business_hours?: string;
  coverage?: string;
  targets: SlaTargetIn[];
}
