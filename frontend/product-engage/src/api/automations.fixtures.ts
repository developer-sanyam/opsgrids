/**
 * Rows typed from the A1 contract, for tests. Nothing imports this in the app: the screens read
 * the live service, and a fixture that shipped would be fabricated data with nothing marking it.
 */

import type {
  AutomationDetailOut,
  AutomationOut,
  Graph,
  RunDetailOut,
  RunOut,
  StatsOut,
  TemplateOption,
  TriggerOut,
} from "./automations.types";
import type { Page } from "./types";

export function page<T>(items: T[]): Page<T> {
  return { items, total: items.length, page: 1, size: 25, pages: items.length === 0 ? 0 : 1 };
}

export const TRIGGERS: TriggerOut[] = [
  {
    event_type: "finance.billing.invoice.issued",
    label: "Invoice issued",
    module: "Finance",
    variables: [
      { path: "payload.customer.name", label: "Customer name", kind: "string" },
      { path: "payload.customer.phone", label: "Customer phone", kind: "string" },
      { path: "payload.invoice.total", label: "Invoice total", kind: "number" },
    ],
  },
  {
    event_type: "crm.lead.created",
    label: "Lead created",
    module: "CRM",
    variables: [{ path: "payload.lead.name", label: "Lead name", kind: "string" }],
  },
];

export const WHATSAPP_TEMPLATE: TemplateOption = {
  ref: "payment_reminder",
  label: "payment_reminder",
  language: "en",
  preview: "Hi {{1}}, your invoice total is {{2}}.",
  variables: ["1", "2"],
};

export const EMAIL_TEMPLATE: TemplateOption = {
  ref: "order_shipped",
  label: "order_shipped",
  language: null,
  preview: "Your order {{order_number}} has shipped",
  variables: ["order_number"],
};

export const AUTOMATION_ACTIVE: AutomationOut = {
  id: "11111111-1111-4111-8111-111111111111",
  name: "Invoice Payment Reminder",
  description: "Remind customers after an invoice is issued.",
  status: "active",
  trigger_event: "finance.billing.invoice.issued",
  module: "Finance",
  version: 2,
  executions_30d: 248,
  success_rate_30d: 0.893,
  last_run_at: "2026-09-25T10:00:00Z",
  updated_at: "2026-09-24T08:00:00Z",
};

export const AUTOMATION_PAUSED: AutomationOut = {
  ...AUTOMATION_ACTIVE,
  id: "22222222-2222-4222-8222-222222222222",
  name: "Ticket Acknowledgement",
  description: "",
  status: "paused",
  module: "Support",
  trigger_event: "support.ticket.created",
  executions_30d: 156,
};

export const AUTOMATION_DRAFT: AutomationOut = {
  ...AUTOMATION_ACTIVE,
  id: "33333333-3333-4333-8333-333333333333",
  name: "Welcome New Customer",
  status: "draft",
  module: "CRM",
  trigger_event: "crm.lead.created",
  version: null,
  executions_30d: 0,
  success_rate_30d: null,
  last_run_at: null,
};

export const DRAFT_GRAPH: Graph = {
  nodes: [
    { id: "trigger", type: "trigger", config: { event_type: "finance.billing.invoice.issued" } },
    { id: "wait_1", type: "wait", config: { seconds: 600 } },
  ],
  edges: [{ from: "trigger", to: "wait_1" }],
};

export function detail(row: AutomationOut, graph: Graph, problems: AutomationDetailOut["validation"]["problems"] = []): AutomationDetailOut {
  return { ...row, graph, validation: { ok: problems.length === 0, problems } };
}

export const RUN_FAILED: RunOut = {
  id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  automation_id: AUTOMATION_ACTIVE.id,
  version: 2,
  state: "failed",
  started_at: "2026-09-25T09:00:00Z",
  finished_at: "2026-09-25T09:00:04Z",
  source_event_id: "evt-1001",
  subject: "R*** S***",
  failure_code: "recipient_not_opted_in",
  next_wake_at: null,
};

export const RUN_WAITING: RunOut = {
  ...RUN_FAILED,
  id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
  state: "waiting",
  finished_at: null,
  failure_code: null,
  next_wake_at: "2026-09-27T09:00:00Z",
  subject: "A*** K***",
};

export const RUN_DETAIL_FAILED: RunDetailOut = {
  ...RUN_FAILED,
  idempotency_key: "auto-1:evt-1001",
  steps: [
    { node_id: "trigger", type: "trigger", state: "done", started_at: "2026-09-25T09:00:00Z", finished_at: "2026-09-25T09:00:00Z", attempt: 1, detail: null, message_id: null },
    { node_id: "send_message_1", type: "send_message", state: "failed", started_at: "2026-09-25T09:00:01Z", finished_at: "2026-09-25T09:00:04Z", attempt: 3, detail: "Recipient has not opted in to WhatsApp.", message_id: null },
  ],
};

export const STATS: StatsOut = {
  executions: 2841,
  successful: 2438,
  failed: 403,
  avg_duration_ms: 4200,
  series: [
    { date: "2026-09-24", successful: 90, failed: 10 },
    { date: "2026-09-25", successful: 100, failed: 4 },
  ],
  failures_by_reason: [{ code: "not_on_whatsapp", label: "Number not on WhatsApp", count: 118 }],
  per_automation: [
    { automation_id: AUTOMATION_ACTIVE.id, name: "Invoice Payment Reminder", executions: 618, success_rate: 0.843, failed: 97, avg_duration_ms: 5200, last_run_at: "2026-09-25T10:00:00Z" },
  ],
};
