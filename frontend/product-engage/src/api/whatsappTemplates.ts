/**
 * WhatsApp template management, typed to the W2 contract (`API-CONTRACT.md`, "W2: WhatsApp
 * templates"). Every path is under `/api/v1/engage/whatsapp/templates`, served by
 * `services/notification` behind the `engage` entitlement.
 *
 * Nothing here decides a status. `status`, `quality` and `rejection_reason` are Meta's answer as
 * the service last read it — from a sync or a webhook — and a screen that renders them is reading,
 * never claiming. A successful create is `pending`: submitted, not approved.
 *
 * There is no fixture branch: unlike the older reads in `client.ts`, this module has no sample
 * data to fall back to, because a fabricated approval status is the one thing these screens must
 * never show.
 */

import { ApiError, apiRequest } from "@opsgrids/client";

import { USE_FIXTURES, fixturePage, refuseWithoutService, settle } from "./client";
import { WA_TEMPLATES, waTemplateDetail } from "./sample.fixtures";

import type { Page, TemplateCategory, TemplateStatus } from "./types";

export type { TemplateCategory, TemplateStatus } from "./types";

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

const BASE = "/engage/whatsapp/templates";

function path(id: string, suffix = ""): string {
  return `${BASE}/${encodeURIComponent(id)}${suffix}`;
}

/** `GET /engage/whatsapp/templates?category&status&q&page`. A blank filter is left off the wire. */
export async function listTemplates(query: TemplateListQuery = {}): Promise<Page<WhatsAppTemplate>> {
  if (USE_FIXTURES) {
    const q = (query.q ?? "").trim().toLowerCase();
    const rows = WA_TEMPLATES.filter(
      (t) =>
        (query.category === undefined || t.category === query.category) &&
        (query.status === undefined || t.status === query.status) &&
        (q === "" || t.id.toLowerCase().includes(q) || t.body_preview.toLowerCase().includes(q)),
    );
    return settle(fixturePage(rows));
  }
  const params = new URLSearchParams();
  if (query.category !== undefined) params.set("category", query.category);
  if (query.status !== undefined) params.set("status", query.status);
  const q = query.q?.trim();
  if (q !== undefined && q !== "") params.set("q", q);
  if (query.page !== undefined && query.page > 1) params.set("page", String(query.page));
  if (query.size !== undefined) params.set("size", String(query.size));
  const qs = params.toString();
  return apiRequest<Page<WhatsAppTemplate>>(qs === "" ? BASE : `${BASE}?${qs}`);
}

/** `GET /engage/whatsapp/templates/{id}` — the row plus its approval history. */
export async function getTemplate(id: string): Promise<WhatsAppTemplateDetail> {
  if (USE_FIXTURES) {
    const found = waTemplateDetail(id);
    if (found === null) throw new ApiError(404, `No such template: ${id}`, null);
    return settle(found);
  }
  return apiRequest<WhatsAppTemplateDetail>(path(id));
}

/** `POST /engage/whatsapp/templates/sync` — pull every template from Meta. */
export async function syncTemplates(): Promise<TemplateSyncResult> {
  if (USE_FIXTURES) refuseWithoutService("Syncing templates from Meta");
  return apiRequest<TemplateSyncResult>(`${BASE}/sync`, { method: "POST" });
}

/**
 * `POST /engage/whatsapp/templates` — submit a new template to Meta. Resolves to the created row
 * with status `pending`. A rule the service refuses is a 422 whose `errors[]` names the field
 * (`components.body.examples`, `name`), which `useMutation` maps onto the controls.
 */
export async function createTemplate(input: CreateTemplateInput): Promise<WhatsAppTemplate> {
  if (USE_FIXTURES) refuseWithoutService("Submitting a template");
  return apiRequest<WhatsAppTemplate>(BASE, { method: "POST", body: input });
}

/** `POST /engage/whatsapp/templates/{id}/preview` — the message with `values` (`{"1": "Rahul"}`) in. */
export async function previewTemplate(
  id: string,
  values: Record<string, string>,
): Promise<TemplatePreview> {
  if (USE_FIXTURES) {
    const found = WA_TEMPLATES.find((t) => t.id === id);
    if (found === undefined) throw new ApiError(404, `No such template: ${id}`, null);
    const text = found.body_preview.replace(/\{\{(\d+)\}\}/g, (whole, n: string) => values[n] ?? whole);
    return settle({ text, buttons: [] });
  }
  return apiRequest<TemplatePreview>(path(id, "/preview"), { method: "POST", body: { values } });
}
