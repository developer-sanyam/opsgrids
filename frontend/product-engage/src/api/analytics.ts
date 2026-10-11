/**
 * Messaging analytics — the M1 (WhatsApp, hub) and E1 (email, campaign) read endpoints, typed field
 * for field from the sprint's API contract. Every path is under `/api/v1/engage`, which
 * `API_BASE` already carries, so the paths below start at `/engage`.
 *
 * **These are reads only, and nothing here computes a figure.** A count is what the service
 * folded; a rate the screens show is derived from two counts on the same response, never from a
 * number this file invents. Money is `{amount_minor, currency}` — integer minor units — and every
 * cost the service returns is an estimate (`estimated: true`): the rate card behind it is not a
 * bill.
 *
 * There is no fixture mode here. These endpoints do not exist in `fixtures.ts`, and a dashboard
 * drawn from made-up counts is exactly the sample-data lie the shared chart card has a chip for.
 */

import { apiRequest } from "@opsgrids/client";

/** `2026-09-01` — the calendar-date form both range parameters take. */
export interface DateRange {
  from: string;
  to: string;
}

/** Integer minor units and an ISO currency. Never a float. */
export interface Money {
  amount_minor: number;
  currency: string;
}

/* ------------------------------ M1: WhatsApp ------------------------------ */

export type WhatsAppCategory = "marketing" | "utility" | "authentication" | "service";

export interface WhatsAppSeriesPoint {
  date: string;
  sent: number;
  delivered: number;
  read: number;
  failed: number;
}

export interface WhatsAppStatusCount {
  status: "read" | "delivered" | "sent" | "failed";
  count: number;
}

export interface FailureReason {
  code: string;
  label: string;
  count: number;
}

/** One line of the activity feed. `kind` is an open string on the WhatsApp side. */
export interface RecentActivity {
  kind: string;
  title: string;
  text: string;
  at: string;
}

export interface WhatsAppTotals {
  active_automations: number;
  approved_templates: number;
  pending_templates: number;
  active_conversations: number;
  unassigned_conversations: number;
}

export interface CostByCategory {
  category: string;
  messages: number;
  amount: Money;
}

/** `estimated` is always `true`: the rate card is an estimate, never an invoice. */
export interface MessagingCost {
  total: Money;
  estimated: true;
  by_category: CostByCategory[];
}

/** `GET /engage/analytics/whatsapp/overview?from&to`. */
export interface WhatsAppOverview {
  sent: number;
  delivered: number;
  read: number;
  failed: number;
  /** Inbound messages on conversations that had an outbound message in the window. */
  replies: number;
  series: WhatsAppSeriesPoint[];
  by_category: { category: WhatsAppCategory; messages: number }[];
  status_breakdown: WhatsAppStatusCount[];
  failures_by_reason: FailureReason[];
  recent_activity: RecentActivity[];
  totals: WhatsAppTotals;
  cost: MessagingCost;
}

/* --------------------------------- E1: email -------------------------------- */

export type EmailActivityKind =
  | "campaign_sent"
  | "high_open_rate"
  | "new_subscriber"
  | "automation_executed";

export interface EmailSeriesPoint {
  date: string;
  sent: number;
  delivered: number;
  opened: number;
  clicked: number;
  failed: number;
}

export interface EmailTotals {
  connected_accounts: number;
  active_automations: number;
  templates: number;
  contacts: number;
  subscribed_contacts: number;
}

/** `GET /engage/analytics/email/overview?from&to`. */
export interface EmailOverview {
  sent: number;
  /** Accepted by the provider — not proof the message reached an inbox. */
  delivered: number;
  opened: number;
  clicked: number;
  failed: number;
  series: EmailSeriesPoint[];
  recent_activity: (Omit<RecentActivity, "kind"> & { kind: EmailActivityKind })[];
  totals: EmailTotals;
}

/** `GET /engage/campaigns/{id}/analytics`. */
export interface CampaignAnalytics {
  recipients: number;
  /** Accepted by the provider — the same meaning as `delivered` on the overview. */
  delivered: number;
  opened: number;
  clicked: number;
  unsubscribed: number;
  bounced: number;
  complaints: number;
  open_rate: number;
  click_rate: number;
  by_device: { device: string; opens: number }[];
  by_link: { url: string; clicks: number }[];
  series: { date: string; opens: number; clicks: number }[];
  /** Whether opens and clicks were tracked for this campaign. Off means "not measured", not zero. */
  tracking: { opens: boolean; clicks: boolean };
  /** The A/B test's two groups and its winner; absent or null for a campaign that is not one. */
  ab_test?: AbTest | null;
}

export interface AbVariant {
  variant: "a" | "b";
  subject: string;
  recipients: number;
  delivered: number;
  opened: number;
  clicked: number;
  open_rate: number;
  click_rate: number;
}

export interface AbTest {
  metric: "open" | "click";
  split_percent: number;
  wait_minutes: number;
  winner: "a" | "b" | null;
  decided_at: string | null;
  variants: AbVariant[];
}

/* ------------------------------------ hub ----------------------------------- */

export type ChannelKey = "whatsapp" | "email" | "sms" | "push";

export interface HubChannel {
  key: ChannelKey;
  connected: boolean;
  detail: string | null;
}

export interface HubWhatsAppKpis {
  sent: number;
  delivered: number;
  read: number;
  failed: number;
  replies: number;
}

export interface HubEmailKpis {
  sent: number;
  delivered: number;
  opened: number;
  clicked: number;
  failed: number;
}

/** `GET /engage/analytics/overview?from&to` — both channels' headline numbers in one read. */
export interface HubOverview {
  channels: HubChannel[];
  whatsapp: HubWhatsAppKpis | null;
  email: HubEmailKpis | null;
  costs: MessagingCost | null;
}

/* -------------------------------- requests -------------------------------- */

function rangeQuery(range: DateRange): string {
  return new URLSearchParams({ from: range.from, to: range.to }).toString();
}

/** The WhatsApp overview and the deeper analytics page read the same endpoint. */
export async function getWhatsAppOverview(range: DateRange): Promise<WhatsAppOverview> {
  return apiRequest<WhatsAppOverview>(`/engage/analytics/whatsapp/overview?${rangeQuery(range)}`);
}

export async function getEmailOverview(range: DateRange): Promise<EmailOverview> {
  return apiRequest<EmailOverview>(`/engage/analytics/email/overview?${rangeQuery(range)}`);
}

export async function getHubOverview(range: DateRange): Promise<HubOverview> {
  return apiRequest<HubOverview>(`/engage/analytics/overview?${rangeQuery(range)}`);
}

export async function getCampaignAnalytics(campaignId: string): Promise<CampaignAnalytics> {
  return apiRequest<CampaignAnalytics>(
    `/engage/campaigns/${encodeURIComponent(campaignId)}/analytics`,
  );
}
