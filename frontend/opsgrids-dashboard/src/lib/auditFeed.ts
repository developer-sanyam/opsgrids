/**
 * The organization audit log, merged from its sources.
 *
 * Every service keeps its own audit trail in its own schema, and each one answers
 * `GET /audit/<service>` newest first with a keyset cursor. Nothing joins them on the server. This
 * module merges the pages here instead, with one buffer and one cursor per source:
 *
 * 1. Top up every live source whose buffer holds fewer than `need` entries (one request each, in
 *    parallel, asking for `need`). A source that comes back short is finished.
 * 2. Take the newest head across all buffers, `need` times.
 *
 * Step 1 is what makes step 2 correct. After it, every unfinished source holds at least `need`
 * entries, so no buffer can run dry part-way through a page while that source still has older rows
 * waiting on the server. An entry is shown only once every unfinished source has shown it nothing
 * newer.
 *
 * A source the organization has not activated (402) or that is not deployed (404) is simply
 * finished. Any other failure is recorded against that source, so the page can still show the
 * rest and say which part is missing.
 */

import { ApiError } from "@opsgrids/client";

import type { AuditEntry, AuditPage, AuditQuery } from "@/api/types";

export interface AuditSource {
  service: string;
  /** The product key the gateway gates the source on; `null` for the always-on platform. */
  product: string | null;
  label: string;
}

/** Every service that serves `/audit/<service>` (`infrastructure/edge/routes.prod.json`). */
export const AUDIT_SOURCES: readonly AuditSource[] = [
  { service: "account", product: null, label: "Organisation" },
  { service: "customer", product: "crm", label: "CRM: companies and contacts" },
  { service: "deal", product: "crm", label: "CRM: deals" },
  { service: "lead", product: "crm", label: "CRM: leads" },
  { service: "employee", product: "hrms", label: "People" },
  { service: "operations", product: "operations", label: "Operations" },
  { service: "notification", product: "engage", label: "Engage" },
  { service: "storefront", product: "storefront", label: "Storefront" },
  { service: "finance", product: "finance", label: "Finance" },
  { service: "auth", product: null, label: "Sign-ins" },
  { service: "approval", product: null, label: "Approvals" },
  { service: "document", product: null, label: "Documents" },
  { service: "subscription", product: null, label: "Subscription" },
  { service: "billing", product: null, label: "Billing" },
  { service: "payment", product: null, label: "Payments" },
  { service: "attendance", product: "hrms", label: "People: attendance" },
  { service: "leave", product: "hrms", label: "People: leave" },
  { service: "project", product: "pulseboard", label: "PulseBoard: projects" },
  { service: "timesheet", product: "pulseboard", label: "PulseBoard: timesheets" },
  { service: "standup", product: "pulseboard", label: "PulseBoard: standups" },
  { service: "projects", product: "projects", label: "Projects" },
  { service: "support", product: "support", label: "Support" },
  { service: "marketing", product: "marketing", label: "Marketing" },
  { service: "itsm", product: "itsm", label: "IT Management" },
];

export function sourceLabel(service: string): string {
  return AUDIT_SOURCES.find((s) => s.service === service)?.label ?? service;
}

/** The sources this organization can have entries in: the platform, plus each active product. */
export function activeSources(entitlements: readonly string[] | null): AuditSource[] {
  return AUDIT_SOURCES.filter((s) => s.product === null || (entitlements ?? []).includes(s.product));
}

export interface SourceState {
  service: string;
  buffer: AuditEntry[];
  cursor: string | null;
  done: boolean;
  error: unknown;
}

export function initialStates(sources: readonly AuditSource[]): SourceState[] {
  return sources.map((s) => ({ service: s.service, buffer: [], cursor: null, done: false, error: null }));
}

export type FetchAuditPage = (service: string, params: AuditQuery) => Promise<AuditPage>;

/** Newest first; the id breaks ties the way the server's `(at, id)` keyset does. */
function newer(a: AuditEntry, b: AuditEntry): boolean {
  const ta = Date.parse(a.at);
  const tb = Date.parse(b.at);
  if (ta !== tb) return ta > tb;
  return a.id > b.id;
}

/** A source the organization cannot have entries in, rather than one that broke. */
function isAbsent(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 402 || error.status === 404);
}

async function topUp(
  state: SourceState,
  need: number,
  filters: Omit<AuditQuery, "cursor" | "limit">,
  fetchPage: FetchAuditPage,
): Promise<SourceState> {
  if (state.done || state.error !== null || state.buffer.length >= need) return state;
  try {
    const page = await fetchPage(state.service, {
      ...filters,
      limit: need,
      ...(state.cursor ? { cursor: state.cursor } : {}),
    });
    return {
      ...state,
      buffer: [...state.buffer, ...page.items],
      cursor: page.next_cursor,
      done: page.next_cursor === null,
    };
  } catch (error) {
    if (isAbsent(error)) return { ...state, done: true };
    return { ...state, error };
  }
}

export interface MergedPage {
  entries: AuditEntry[];
  states: SourceState[];
  /** True while any source still has entries to give. */
  hasMore: boolean;
}

/** The next `need` entries across every source, newest first. */
export async function nextPage(
  states: readonly SourceState[],
  need: number,
  filters: Omit<AuditQuery, "cursor" | "limit">,
  fetchPage: FetchAuditPage,
): Promise<MergedPage> {
  const filled = await Promise.all(states.map((s) => topUp(s, need, filters, fetchPage)));
  const buffers = filled.map((s) => (s.error === null ? [...s.buffer] : []));
  const entries: AuditEntry[] = [];
  while (entries.length < need) {
    let pick = -1;
    buffers.forEach((buffer, i) => {
      const head = buffer[0];
      if (head === undefined) return;
      const best = pick === -1 ? undefined : buffers[pick]?.[0];
      if (best === undefined || newer(head, best)) pick = i;
    });
    if (pick === -1) break;
    const taken = buffers[pick]?.shift();
    if (taken) entries.push(taken);
  }
  const next = filled.map((s, i) => (s.error === null ? { ...s, buffer: buffers[i] ?? [] } : s));
  return {
    entries,
    states: next,
    hasMore: next.some((s) => s.error === null && (s.buffer.length > 0 || !s.done)),
  };
}

/** True when every source that answered refused the caller — the page has nothing to show. */
export function everySourceRefused(states: readonly SourceState[]): boolean {
  return states.length > 0 && states.every((s) => s.error instanceof ApiError && s.error.status === 403);
}

const SIGN_IN_ACTIONS: Record<string, string> = {
  "auth.password_verified": "Signed in with a password",
  "auth.oauth_signed_in": "Signed in with a social account",
  "auth.sso_signed_in": "Signed in with SSO",
  "auth.password_changed": "Password changed",
  "auth.password_change_failed": "Password change failed",
};

/** "member.invited" → "Member invited". */
export function describeAction(action: string): string {
  const known = SIGN_IN_ACTIONS[action];
  if (known) return known;
  const words = action.replace(/[._]+/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}
