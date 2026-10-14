/** The request and response shapes of `./analytics`, kept apart so fixtures can import them without importing the requests. */

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

/** What one automation (its rule code) or inbox conversation (its masked number) cost. */
export interface CostBySource {
  kind: "automation" | "conversation";
  key: string;
  label: string;
  messages: number;
  amount: Money;
}

/** `estimated` is always `true`: the rate card is an estimate, never an invoice. */
export interface MessagingCost {
  total: Money;
  estimated: true;
  by_category: CostByCategory[];
  /** How many sent messages Meta priced on its own delivery receipt (WhatsApp overview only). */
  priced_by_meta?: number;
  /** The most expensive automations and conversations, most expensive first (WhatsApp only). */
  by_source?: CostBySource[];
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

/** Which rates produced a cost, so a reader can tell a placeholder from a real figure. */
export interface RateCardRef {
  source: "platform_default" | "organization" | "mixed";
  placeholder: boolean;
}

/** The hub's cost block (`HubCosts`): one total, split by channel. Not the WhatsApp page's
 *  `MessagingCost`, which splits by template category. */
export interface HubCosts {
  total: Money;
  estimated: true;
  by_channel: { channel: ChannelKey; amount: Money }[];
  rate_card: RateCardRef;
}

/** `GET /engage/analytics/overview?from&to` — both channels' headline numbers in one read. The
 *  service returns each channel's full overview here (`WhatsAppOverview`, and E1's email overview
 *  passed through), so the hub reads only the headline counts and leaves the rest. */
export interface HubOverview {
  channels: HubChannel[];
  whatsapp: WhatsAppOverview | null;
  email: EmailOverview | null;
  costs: HubCosts | null;
}
