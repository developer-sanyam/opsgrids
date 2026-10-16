/**
 * Cold e-mail to leads: the Engage endpoints the Leads list calls (`/engage/lead-campaigns`, and
 * the ordinary campaign `send` / `schedule` after it). Same gateway prefix as every other read.
 * Every gate is the server's; the dialog hides what the caller cannot use and nothing more.
 */

import { apiRequest } from "@opsgrids/client";

import type { LeadSource, LeadStatus } from "./types";

/** What the rep chose: the ticked leads, or everything the Leads list's filter matches. */
export type LeadSelection =
  | { kind: "ids"; ids: string[] }
  | { kind: "filter"; status: LeadStatus | null; source: LeadSource | null };

export interface AudienceCounts {
  selected: number;
  with_email: number;
  /** Converted or disqualified leads are never cold-mailed. */
  skipped_status: number;
  /** Suppressed, unsubscribed or opted out of marketing. */
  excluded: number;
  sendable: number;
  daily_cap: number;
  sent_today: number;
}

export interface LeadAudiencePreview extends AudienceCounts {
  sample: { lead_id: string; full_name: string; subject: string; text_body: string } | null;
  unknown_fields: string[];
}

export interface EmailTemplate {
  id: string;
  subject: string | null;
  body: string;
}

export interface Sender {
  id: string;
  name: string;
  address: string;
  usable: boolean;
}

export interface LeadCampaign extends AudienceCounts {
  campaign: { id: string; name: string; status: string };
}

export interface LeadColdEmail {
  campaign_id: string;
  title: string;
  at: string;
}

function selectionBody(selection: LeadSelection): Record<string, unknown> {
  return selection.kind === "ids"
    ? { lead_ids: selection.ids }
    : {
        ...(selection.status !== null ? { status: selection.status } : {}),
        ...(selection.source !== null ? { source: selection.source } : {}),
      };
}

export function previewLeadEmail(
  selection: LeadSelection,
  draft: { subject: string; text_body: string },
  signal?: AbortSignal,
): Promise<LeadAudiencePreview> {
  return apiRequest<LeadAudiencePreview>("/engage/lead-campaigns/preview", {
    method: "POST",
    body: { ...selectionBody(selection), ...draft },
    signal,
  });
}

export function createLeadCampaign(
  selection: LeadSelection,
  draft: { subject: string; text_body: string; sender_id: string; name: string },
): Promise<LeadCampaign> {
  return apiRequest<LeadCampaign>("/engage/lead-campaigns", {
    method: "POST",
    body: { ...selectionBody(selection), ...draft },
  });
}

export function sendLeadCampaign(campaignId: string): Promise<unknown> {
  return apiRequest(`/engage/campaigns/${encodeURIComponent(campaignId)}/send`, { method: "POST" });
}

export function scheduleLeadCampaign(campaignId: string, scheduledAt: string): Promise<unknown> {
  return apiRequest(`/engage/campaigns/${encodeURIComponent(campaignId)}/schedule`, {
    method: "POST",
    body: { scheduled_at: scheduledAt },
  });
}

export async function listEmailTemplates(): Promise<EmailTemplate[]> {
  const page = await apiRequest<{ items: EmailTemplate[] }>("/engage/email/templates?size=100");
  return page.items;
}

export async function listUsableSenders(): Promise<Sender[]> {
  const page = await apiRequest<{ items: Sender[] }>("/engage/senders?size=100");
  return page.items.filter((s) => s.usable);
}

export function getLeadColdEmails(leadId: string): Promise<LeadColdEmail[]> {
  return apiRequest<LeadColdEmail[]>(
    `/engage/lead-campaigns/leads/${encodeURIComponent(leadId)}/emails`,
  );
}
