import type { Page } from "@/api/types";
import type {
  TemplatePreview,
  WhatsAppTemplate,
  WhatsAppTemplateDetail,
} from "@/api/whatsappTemplates";

/** Rows typed from the W2 contract, shared by this feature's tests. */

export const READ_AND_WRITE = [
  "engage.whatsapp.template.read:ORG",
  "engage.whatsapp.template.write:ORG",
];

export const APPROVED: WhatsAppTemplate = {
  id: "order_shipped",
  meta_template_id: "1187364502983471",
  category: "utility",
  language: "en_US",
  status: "approved",
  quality: "high",
  rejection_reason: null,
  components: [],
  body_preview: "Hello {{1}}, your order {{2}} has shipped.",
  variables: [
    { index: 1, name: "customer_name", kind: "text", example: "Rahul" },
    { index: 2, name: "order_number", kind: "text", example: "#ORD-2938" },
  ],
  submitted_at: "2026-09-24T05:32:00Z",
  updated_at: "2026-09-24T05:39:00Z",
};

export const PENDING: WhatsAppTemplate = {
  ...APPROVED,
  id: "feedback_request",
  meta_template_id: null,
  category: "marketing",
  status: "pending",
  quality: "unknown",
  body_preview: "Hi {{1}}, how did we do?",
  variables: [{ index: 1, name: "customer_name", kind: "text", example: "Asha" }],
};

export const REJECTED: WhatsAppTemplate = {
  ...APPROVED,
  id: "cod_confirmation",
  status: "rejected",
  quality: "unknown",
  rejection_reason: "The message asks for payment details, which Meta does not allow here.",
};

export const DETAIL: WhatsAppTemplateDetail = {
  ...APPROVED,
  history: [
    { status: "pending", at: "2026-09-24T05:32:00Z", source: "submit" },
    { status: "approved", at: "2026-09-24T05:39:00Z", source: "meta_webhook" },
  ],
};

export const PREVIEW: TemplatePreview = {
  text: "Hello Rahul, your order #ORD-2938 has shipped.",
  buttons: [{ type: "url", text: "Track order", url: "https://example.com/track" }],
};

export function page<T>(items: T[], overrides: Partial<Page<T>> = {}): Page<T> {
  return { items, total: items.length, page: 1, size: 20, pages: items.length ? 1 : 0, ...overrides };
}
