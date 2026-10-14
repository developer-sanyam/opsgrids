/**
 * Sample data for the analytics reads, used only when the app runs with `VITE_USE_FIXTURES=true`
 * (dev builds; `USE_FIXTURES` compiles to `false` in production). Typed from the contract, in the
 * shape the service sends, so a dashboard behaves the same either side of the flag.
 *
 * The daily series follows the requested range, so changing the range in the UI visibly changes
 * the chart. The figures are a deterministic function of the day, not random: a screenshot taken
 * twice is the same screenshot.
 */

import type {
  CampaignAnalytics,
  DateRange,
  EmailOverview,
  EmailSeriesPoint,
  HubOverview,
  WhatsAppOverview,
  WhatsAppSeriesPoint,
} from "./analytics";

const MAX_DAYS = 366;

/** Every calendar day from `range.from` to `range.to`, inclusive, as `YYYY-MM-DD`. */
function daysIn(range: DateRange): string[] {
  const start = new Date(`${range.from}T00:00:00Z`);
  const end = new Date(`${range.to}T00:00:00Z`);
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime()) || end < start) return [];
  const days: string[] = [];
  for (let at = start; at <= end && days.length < MAX_DAYS; at = new Date(at.getTime() + 86_400_000)) {
    days.push(at.toISOString().slice(0, 10));
  }
  return days;
}

/** A repeatable wobble in 0.7..1.3 so the lines are not flat. */
function wobble(index: number): number {
  return 1 + 0.3 * Math.sin(index * 1.7);
}

function whatsAppSeries(range: DateRange): WhatsAppSeriesPoint[] {
  return daysIn(range).map((date, index) => {
    const sent = Math.round(420 * wobble(index));
    const delivered = Math.round(sent * 0.93);
    return { date, sent, delivered, read: Math.round(delivered * 0.72), failed: sent - delivered };
  });
}

function emailSeries(range: DateRange): EmailSeriesPoint[] {
  return daysIn(range).map((date, index) => {
    const sent = Math.round(1100 * wobble(index));
    const delivered = Math.round(sent * 0.97);
    const opened = Math.round(delivered * 0.4);
    return { date, sent, delivered, opened, clicked: Math.round(opened * 0.3), failed: sent - delivered };
  });
}

function sum<T>(rows: T[], pick: (row: T) => number): number {
  return rows.reduce((total, row) => total + pick(row), 0);
}

export function sampleWhatsAppOverview(range: DateRange): WhatsAppOverview {
  const series = whatsAppSeries(range);
  const sent = sum(series, (p) => p.sent);
  const delivered = sum(series, (p) => p.delivered);
  const read = sum(series, (p) => p.read);
  const failed = sum(series, (p) => p.failed);
  const marketing = Math.round(sent * 0.7);
  const utility = Math.round(sent * 0.22);
  const authentication = Math.round(sent * 0.06);
  const service = sent - marketing - utility - authentication;
  const cost = (messages: number, minor: number) => ({
    messages,
    amount: { amount_minor: messages * minor, currency: "INR" },
  });
  const byCategory = [
    { category: "marketing", ...cost(marketing, 78) },
    { category: "utility", ...cost(utility, 12) },
    { category: "authentication", ...cost(authentication, 12) },
  ];
  return {
    sent,
    delivered,
    read,
    failed,
    replies: Math.round(read * 0.25),
    series,
    by_category: [
      { category: "marketing", messages: marketing },
      { category: "utility", messages: utility },
      { category: "authentication", messages: authentication },
      { category: "service", messages: service },
    ],
    status_breakdown: [
      { status: "read", count: read },
      { status: "delivered", count: delivered - read },
      { status: "sent", count: 0 },
      { status: "failed", count: failed },
    ],
    failures_by_reason:
      failed === 0
        ? []
        : [
            { code: "131026", label: "Recipient not on WhatsApp", count: Math.round(failed * 0.65) },
            {
              code: "131049",
              label: "Blocked by Meta to protect engagement",
              count: failed - Math.round(failed * 0.65),
            },
          ],
    recent_activity: [
      { kind: "message_delivered", title: "Message delivered", text: "Order shipped - #ORD-2938", at: "2026-10-13T05:58:00Z" },
      { kind: "incoming_message", title: "New incoming message", text: "From Priya M.", at: "2026-10-13T05:55:00Z" },
      { kind: "template_approved", title: "Template approved", text: "delivery_confirmed", at: "2026-10-12T11:20:00Z" },
    ],
    totals: {
      active_automations: 3,
      approved_templates: 2,
      pending_templates: 1,
      active_conversations: 4,
      unassigned_conversations: 1,
    },
    cost: {
      total: {
        amount_minor: byCategory.reduce((total, row) => total + row.amount.amount_minor, 0),
        currency: "INR",
      },
      estimated: true,
      by_category: byCategory,
      priced_by_meta: Math.round(sent * 0.4),
      by_source: [
        { kind: "automation", key: "invoice_reminder", label: "Invoice Payment Reminder", messages: Math.round(sent * 0.2), amount: { amount_minor: Math.round(sent * 0.2) * 12, currency: "INR" } },
        { kind: "conversation", key: "+91 ••••• 4321", label: "+91 ••••• 4321", messages: 14, amount: { amount_minor: 14 * 12, currency: "INR" } },
      ],
    },
  };
}

export function sampleEmailOverview(range: DateRange): EmailOverview {
  const series = emailSeries(range);
  return {
    sent: sum(series, (p) => p.sent),
    delivered: sum(series, (p) => p.delivered),
    opened: sum(series, (p) => p.opened),
    clicked: sum(series, (p) => p.clicked),
    failed: sum(series, (p) => p.failed),
    series,
    recent_activity: [
      { kind: "campaign_sent", title: "Campaign sent", text: "August product update", at: "2026-10-13T05:58:00Z" },
      { kind: "new_subscriber", title: "New subscriber", text: "r***@example.com", at: "2026-10-13T05:45:00Z" },
      { kind: "automation_executed", title: "Automation executed", text: "Welcome New Customer", at: "2026-10-12T16:10:00Z" },
    ],
    totals: {
      connected_accounts: 1,
      active_automations: 2,
      templates: 3,
      contacts: 2,
      subscribed_contacts: 2,
    },
  };
}

export function sampleHubOverview(range: DateRange): HubOverview {
  const whatsapp = sampleWhatsAppOverview(range);
  return {
    channels: [
      { key: "whatsapp", connected: true, detail: "+91 ••••• 4321 · Quality green" },
      { key: "email", connected: true, detail: "SMTP · notifications.example.com" },
      { key: "sms", connected: false, detail: null },
      { key: "push", connected: false, detail: null },
    ],
    whatsapp,
    email: sampleEmailOverview(range),
    costs: {
      total: whatsapp.cost.total,
      estimated: true,
      by_channel: [
        { channel: "whatsapp", amount: whatsapp.cost.total },
        { channel: "email", amount: { amount_minor: 0, currency: whatsapp.cost.total.currency } },
      ],
      rate_card: { source: "platform_default", placeholder: true },
    },
  };
}

export function sampleCampaignAnalytics(): CampaignAnalytics {
  return {
    recipients: 2400,
    delivered: 2352,
    opened: 988,
    clicked: 212,
    unsubscribed: 9,
    bounced: 41,
    complaints: 1,
    open_rate: 0.42,
    click_rate: 0.09,
    by_device: [
      { device: "desktop", opens: 520 },
      { device: "mobile", opens: 410 },
      { device: "tablet", opens: 58 },
    ],
    by_link: [
      { url: "https://shop.example.com/sale", clicks: 128 },
      { url: "https://shop.example.com/shipping", clicks: 84 },
    ],
    series: [
      { date: "2026-08-20", opens: 410, clicks: 90 },
      { date: "2026-08-21", opens: 330, clicks: 70 },
      { date: "2026-08-22", opens: 248, clicks: 52 },
    ],
    tracking: { opens: true, clicks: true },
    ab_test: null,
  };
}
