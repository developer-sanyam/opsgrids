/**
 * Sample rows for the reads that `fixtures.ts` does not cover: the WhatsApp connect/settings
 * screens, the WhatsApp template library, email templates, and automations. Used only when the app
 * runs with `VITE_USE_FIXTURES=true` (dev builds). Typed from the contract; the ids are the ones
 * `fixtures.ts` and `automations.fixtures.ts` already use, so a link from one screen lands on a row
 * that exists on the next.
 *
 * Writes stay refused in fixture mode (see `refuseWithoutService` in `client.ts`): nothing here is
 * ever changed by the browser.
 */

import type { AutomationDetailOut, Graph, RunOut, TemplateOption } from "./automations";
import {
  AUTOMATION_ACTIVE,
  AUTOMATION_DRAFT,
  AUTOMATION_PAUSED,
  DRAFT_GRAPH,
  RUN_FAILED,
  RUN_WAITING,
  detail,
} from "./automations.fixtures";
import type { EmailTemplateOut } from "./email";
import type { SignupConfig, WhatsAppHealth, WhatsAppNumbersOut } from "./whatsappSignup";
import type { WhatsAppTemplate, WhatsAppTemplateDetail } from "./whatsappTemplates";

/* ------------------------------- whatsapp connect ------------------------------- */

export const SIGNUP_CONFIG: SignupConfig = {
  enabled: false,
  app_id: null,
  config_id: null,
  graph_version: "v21.0",
  reason: "not_configured",
};

export const WHATSAPP_NUMBERS: WhatsAppNumbersOut = {
  items: [
    {
      id: "7e1d2c3b-4a59-4687-9a0b-c1d2e3f40516",
      phone_number_id: "109827364501928",
      display_phone_number: "+91 ••••• 4321",
      verified_name: "Acme Logistics",
      name_status: "approved",
      quality_rating: "green",
      messaging_limit: "TIER_1K",
      status: "connected",
      is_default: true,
      registration: "registered",
      code_verification_status: "VERIFIED",
      throughput_level: "STANDARD",
    },
  ],
};

export const WHATSAPP_HEALTH: WhatsAppHealth = {
  connected: true,
  token: { state: "valid", checked_at: "2026-10-13T06:00:00Z", identity_revoked_at: null },
  webhook: { state: "receiving", last_event_at: "2026-10-13T05:58:00Z" },
  templates: { count: 3, synced_at: "2026-10-12T09:00:00Z" },
  number: { quality_rating: "green", messaging_limit: "TIER_1K" },
};

/* ------------------------------ whatsapp templates ------------------------------ */

const BODY = (text: string) => ({ body: { text } });

export const WA_TEMPLATES: WhatsAppTemplate[] = [
  {
    id: "delivery_confirmed",
    meta_template_id: "1187364502983471",
    category: "utility",
    language: "en",
    status: "approved",
    quality: "high",
    rejection_reason: null,
    components: BODY("Hi {{1}}, your shipment is out for delivery today."),
    body_preview: "Hi {{1}}, your shipment is out for delivery today.",
    variables: [{ index: 1, name: "customer_name", kind: "text", example: "Priya" }],
    submitted_at: "2026-08-30T09:14:22Z",
    updated_at: "2026-09-21T04:12:09Z",
  },
  {
    id: "payment_reminder",
    meta_template_id: null,
    category: "utility",
    language: "en",
    status: "pending",
    quality: "unknown",
    rejection_reason: null,
    components: BODY("Reminder: {{1}} is due on your account."),
    body_preview: "Reminder: {{1}} is due on your account.",
    variables: [{ index: 1, name: "amount_due", kind: "text", example: "₹4,200" }],
    submitted_at: "2026-09-18T15:40:02Z",
    updated_at: "2026-09-20T11:03:44Z",
  },
  {
    id: "promo_diwali",
    meta_template_id: "1298374650192837",
    category: "marketing",
    language: "en",
    status: "rejected",
    quality: "unknown",
    rejection_reason: "Promotional content requires an opt-in confirmation step.",
    components: BODY("Celebrate with us - 20% off this Diwali."),
    body_preview: "Celebrate with us - 20% off this Diwali.",
    variables: [],
    submitted_at: "2026-09-10T10:00:00Z",
    updated_at: "2026-09-15T07:22:51Z",
  },
];

export function waTemplateDetail(id: string): WhatsAppTemplateDetail | null {
  const row = WA_TEMPLATES.find((t) => t.id === id);
  if (row === undefined) return null;
  return {
    ...row,
    history: [
      { status: "pending", at: row.submitted_at ?? row.updated_at, source: "submit" },
      ...(row.status === "pending" ? [] : [{ status: row.status, at: row.updated_at, source: "meta_webhook" as const }]),
    ],
  };
}

/* ------------------------------- email templates -------------------------------- */

export const EMAIL_TEMPLATES: EmailTemplateOut[] = [
  {
    id: "order_shipped",
    channel: "email",
    subject: "Your order {{order_number}} has shipped",
    body: "Hi {{customer_name}},\n\nYour order {{order_number}} is on its way.\n\nThanks,\nAcme Logistics",
    description: "Sent when an order leaves the warehouse.",
    placeholders: ["order_number", "customer_name"],
    created_at: "2026-08-12T09:00:00Z",
    updated_at: "2026-09-01T10:30:00Z",
  },
  {
    id: "welcome",
    channel: "email",
    subject: "Welcome to Acme",
    body: "Hi {{customer_name}},\n\nWelcome aboard.",
    description: "",
    placeholders: ["customer_name"],
    created_at: "2026-08-01T09:00:00Z",
    updated_at: "2026-08-01T09:00:00Z",
  },
  {
    id: "invoice_due",
    channel: "email",
    subject: null,
    body: "Invoice {{invoice_number}} is due on {{due_date}}.",
    description: "Plain reminder without a subject line.",
    placeholders: ["invoice_number", "due_date"],
    created_at: "2026-09-05T09:00:00Z",
    updated_at: "2026-09-05T09:00:00Z",
  },
];

/* -------------------------------- automations ---------------------------------- */

export const AUTOMATIONS = [AUTOMATION_ACTIVE, AUTOMATION_PAUSED, AUTOMATION_DRAFT];

const SEND_GRAPH: Graph = {
  nodes: [
    ...DRAFT_GRAPH.nodes,
    {
      id: "send_message_1",
      type: "send_message",
      config: {
        channel: "whatsapp",
        template: { ref: "payment_reminder", language: "en" },
        recipient_path: "payload.customer.phone",
        variable_map: { "1": "payload.invoice.total" },
        purpose: "transactional",
      },
    },
    { id: "end_1", type: "end", config: {} },
  ],
  edges: [
    { from: "trigger", to: "wait_1" },
    { from: "wait_1", to: "send_message_1" },
    { from: "send_message_1", to: "end_1" },
  ],
};

export function automationDetail(id: string): AutomationDetailOut | null {
  const row = AUTOMATIONS.find((a) => a.id === id);
  if (row === undefined) return null;
  return detail(row, row.status === "draft" ? DRAFT_GRAPH : SEND_GRAPH);
}

export const AUTOMATION_RUNS: RunOut[] = [RUN_FAILED, RUN_WAITING];

export const WHATSAPP_TEMPLATE_OPTIONS: TemplateOption[] = WA_TEMPLATES.filter(
  (t) => t.status === "approved",
).map((t) => ({
  ref: t.id,
  label: t.id,
  language: t.language,
  preview: t.body_preview,
  variables: t.variables.map((v) => String(v.index)),
}));

export const EMAIL_TEMPLATE_OPTIONS: TemplateOption[] = EMAIL_TEMPLATES.map((t) => ({
  ref: t.id,
  label: t.id,
  language: null,
  preview: t.subject ?? t.description,
  variables: t.placeholders,
}));
