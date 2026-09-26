import type {
  SignupConfig,
  SignupStatusOut,
  WhatsAppHealth,
  WhatsAppNumber,
} from "@/api/whatsappSignup";
import type { Page, ProviderTemplateOut } from "@/api/types";

/** Test fixtures, typed from the W1 contract: a field the contract does not have does not compile. */

export const SETUP_ID = "7c1f0d2e-3a4b-4c5d-8e6f-1a2b3c4d5e6f";

export const CONFIG_ENABLED: SignupConfig = {
  enabled: true,
  app_id: "1234567890",
  config_id: "9876543210",
  graph_version: "v21.0",
};

export const CONFIG_DISABLED: SignupConfig = {
  enabled: false,
  app_id: null,
  config_id: null,
  graph_version: "v21.0",
  reason: "not_configured",
};

export const MAIN_NUMBER: WhatsAppNumber = {
  id: "0a1b2c3d-0000-4000-8000-000000000001",
  phone_number_id: "109827364501928",
  display_phone_number: "+91 ••••• ••210",
  verified_name: "Acme Corp",
  name_status: "approved",
  quality_rating: "green",
  messaging_limit: "TIER_1K",
  status: "connected",
  is_default: true,
};

export const SUPPORT_NUMBER: WhatsAppNumber = {
  id: "0a1b2c3d-0000-4000-8000-000000000002",
  phone_number_id: "109827364501929",
  display_phone_number: "+91 ••••• ••789",
  verified_name: "Acme Support",
  name_status: "pending_review",
  quality_rating: "unknown",
  messaging_limit: "TIER_250",
  status: "connected",
  is_default: false,
};

export const PENDING_NUMBER: WhatsAppNumber = {
  id: "0a1b2c3d-0000-4000-8000-000000000003",
  phone_number_id: "109827364501930",
  display_phone_number: "+91 22 ••••• 0142",
  verified_name: null,
  name_status: "none",
  quality_rating: "unknown",
  messaging_limit: "unknown",
  status: "pending_verification",
  is_default: false,
};

export const HEALTH_OK: WhatsAppHealth = {
  connected: true,
  token: { state: "valid", checked_at: "2026-09-26T05:50:00Z" },
  webhook: { state: "receiving", last_event_at: "2026-09-26T05:58:00Z" },
  templates: { count: 24, synced_at: "2026-09-26T05:48:00Z" },
  number: { quality_rating: "green", messaging_limit: "TIER_1K" },
};

export const HEALTH_DISCONNECTED: WhatsAppHealth = {
  connected: false,
  token: { state: "unknown", checked_at: null },
  webhook: { state: "unverified", last_event_at: null },
  templates: { count: 24, synced_at: "2026-09-26T05:48:00Z" },
  number: null,
};

function status(overrides: Partial<SignupStatusOut>): SignupStatusOut {
  return {
    setup_id: SETUP_ID,
    status: "running",
    error_code: null,
    steps: [
      { key: "token", label: "Access token saved", state: "done", detail: null },
      { key: "number", label: "Phone number registered", state: "done", detail: null },
      { key: "webhook", label: "Webhook subscribed", state: "done", detail: null },
      { key: "templates", label: "Syncing message templates", state: "running", detail: "18 of 24 templates imported." },
      { key: "verify", label: "Checking the connection end to end", state: "waiting", detail: null },
    ],
    ...overrides,
  };
}

export const SETUP_RUNNING = status({});

export const SETUP_DONE = status({
  status: "done",
  steps: SETUP_RUNNING.steps.map((step) => ({ ...step, state: "done" as const })),
});

export const SETUP_FAILED = status({
  status: "failed",
  error_code: "webhook_subscribe_failed",
  steps: [
    { key: "token", label: "Access token saved", state: "done", detail: null },
    { key: "number", label: "Phone number registered", state: "done", detail: null },
    {
      key: "webhook",
      label: "Webhook subscribed",
      state: "failed",
      detail: "Meta refused the webhook subscription.",
    },
    { key: "templates", label: "Syncing message templates", state: "waiting", detail: null },
    { key: "verify", label: "Checking the connection end to end", state: "waiting", detail: null },
  ],
});

export const APPROVED_TEMPLATE: ProviderTemplateOut = {
  id: "hello_world",
  channel: "whatsapp",
  provider_name: "meta",
  provider_template_id: "1001",
  language: "en",
  category: "utility",
  parameter_format: "positional",
  status: "approved",
  status_reason: null,
  status_checked_at: "2026-09-26T05:48:00Z",
  variables: [],
  preview_text: "Hello World",
  sendable: true,
  created_at: "2026-09-20T09:00:00Z",
  updated_at: "2026-09-26T05:48:00Z",
};

export const PENDING_TEMPLATE: ProviderTemplateOut = {
  ...APPROVED_TEMPLATE,
  id: "order_shipped",
  status: "pending",
  sendable: false,
};

export function templatePage(items: ProviderTemplateOut[]): Page<ProviderTemplateOut> {
  return { items, total: items.length, page: 1, size: 200, pages: items.length > 0 ? 1 : 0 };
}
