/** The request and response shapes of `./whatsappTemplates`, kept apart so fixtures can import them without importing the requests. */
import type { TemplateCategory, TemplateStatus } from "./types";

export type TemplateQuality = "high" | "medium" | "low" | "unknown";

export type TemplateVariableKind = "text" | "url" | "date" | "number";

export type TemplateHeaderType = "text" | "image" | "video" | "document";

export type TemplateButtonType = "quick_reply" | "url" | "phone";

export type TemplateHistorySource = "meta_webhook" | "sync" | "submit";

/** One `{{n}}` placeholder as the service resolved it. `example` is what Meta reviewed. */
export interface TemplateVariable {
  index: number;
  name: string;
  kind: TemplateVariableKind;
  example: string;
}

/** A row of `GET /engage/whatsapp/templates`. Fields beyond these may arrive and are ignored. */
export interface WhatsAppTemplate {
  /** The template's own identifier — its name — and the path segment for every per-template call. */
  id: string;
  /** Not in the contract: shown in preference to `id` if the service ever adds one. */
  name?: string;
  meta_template_id: string | null;
  category: TemplateCategory;
  language: string;
  status: TemplateStatus;
  quality: TemplateQuality;
  rejection_reason: string | null;
  /** Meta's components JSON, opaque here: this app renders through `previewTemplate`, never by
   *  parsing it, so a change to Meta's shape cannot break a screen. */
  components: unknown;
  body_preview: string;
  variables: TemplateVariable[];
  submitted_at: string | null;
  updated_at: string;
}

export interface TemplateHistoryEntry {
  status: TemplateStatus;
  at: string;
  source: TemplateHistorySource;
}

/** `GET /engage/whatsapp/templates/{id}`. */
export interface WhatsAppTemplateDetail extends WhatsAppTemplate {
  history: TemplateHistoryEntry[];
}

export interface TemplateButton {
  type: TemplateButtonType;
  text: string;
  url?: string;
  phone?: string;
}

export interface TemplateHeaderInput {
  type: TemplateHeaderType;
  text?: string;
  example?: string;
}

/** The `components` object of `POST /engage/whatsapp/templates`. */
export interface TemplateComponentsInput {
  header?: TemplateHeaderInput;
  body: { text: string; examples: string[] };
  footer?: { text: string };
  buttons?: TemplateButton[];
}

/** `POST /engage/whatsapp/templates`. Unknown fields are refused with a 422. */
export interface CreateTemplateInput {
  name: string;
  category: TemplateCategory;
  language: string;
  components: TemplateComponentsInput;
}

/** `POST /engage/whatsapp/templates/sync`. */
export interface TemplateSyncResult {
  synced: number;
  created: number;
  updated: number;
  removed: number;
}

/** `POST /engage/whatsapp/templates/{id}/preview`. */
export interface TemplatePreview {
  text: string;
  buttons: TemplateButton[];
}

export interface TemplateListQuery {
  category?: TemplateCategory;
  status?: TemplateStatus;
  q?: string;
  page?: number;
  size?: number;
}
