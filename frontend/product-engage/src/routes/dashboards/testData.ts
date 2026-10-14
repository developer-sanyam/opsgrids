import type {
  CampaignAnalytics,
  EmailOverview,
  HubOverview,
  WhatsAppOverview,
} from "@/api/analytics";

/**
 * Fixtures for the dashboard tests, typed from the contract so a field the service renames stops
 * compiling here first. Only tests import this file.
 */

export const WHATSAPP: WhatsAppOverview = {
  sent: 12842,
  delivered: 11921,
  read: 8643,
  failed: 134,
  replies: 2184,
  series: [
    { date: "2026-09-01", sent: 620, delivered: 580, read: 400, failed: 20 },
    { date: "2026-09-02", sent: 900, delivered: 840, read: 610, failed: 30 },
  ],
  by_category: [
    { category: "marketing", messages: 9000 },
    { category: "utility", messages: 3000 },
    { category: "authentication", messages: 800 },
    { category: "service", messages: 42 },
  ],
  status_breakdown: [
    { status: "read", count: 8643 },
    { status: "delivered", count: 3278 },
    { status: "sent", count: 787 },
    { status: "failed", count: 134 },
  ],
  failures_by_reason: [
    { code: "131026", label: "Recipient not on WhatsApp", count: 90 },
    { code: "131049", label: "Blocked by Meta to protect engagement", count: 44 },
  ],
  recent_activity: [
    { kind: "message_delivered", title: "Message delivered", text: "Order shipped - #ORD-2938", at: "2026-09-26T05:58:00Z" },
    { kind: "incoming_message", title: "New incoming message", text: "From Rahul S.", at: "2026-09-26T05:55:00Z" },
  ],
  totals: {
    active_automations: 18,
    approved_templates: 24,
    pending_templates: 8,
    active_conversations: 142,
    unassigned_conversations: 28,
  },
  cost: {
    total: { amount_minor: 384250, currency: "INR" },
    estimated: true,
    by_category: [
      { category: "marketing", messages: 9000, amount: { amount_minor: 706140, currency: "INR" } },
      { category: "utility", messages: 3000, amount: { amount_minor: 34500, currency: "INR" } },
    ],
  },
};

export const WHATSAPP_EMPTY: WhatsAppOverview = {
  sent: 0,
  delivered: 0,
  read: 0,
  failed: 0,
  replies: 0,
  series: [],
  by_category: [],
  status_breakdown: [],
  failures_by_reason: [],
  recent_activity: [],
  totals: {
    active_automations: 0,
    approved_templates: 0,
    pending_templates: 0,
    active_conversations: 0,
    unassigned_conversations: 0,
  },
  cost: { total: { amount_minor: 0, currency: "INR" }, estimated: true, by_category: [] },
};

export const EMAIL: EmailOverview = {
  sent: 48392,
  delivered: 46821,
  opened: 18642,
  clicked: 5831,
  failed: 421,
  series: [
    { date: "2026-09-01", sent: 900, delivered: 870, opened: 340, clicked: 110, failed: 8 },
    { date: "2026-09-02", sent: 1300, delivered: 1258, opened: 500, clicked: 160, failed: 12 },
  ],
  recent_activity: [
    { kind: "campaign_sent", title: "Campaign sent", text: "Festive Offer Campaign", at: "2026-09-26T05:58:00Z" },
    { kind: "new_subscriber", title: "New subscriber", text: "r***@example.com", at: "2026-09-26T05:45:00Z" },
  ],
  totals: {
    connected_accounts: 2,
    active_automations: 12,
    templates: 28,
    contacts: 15206,
    subscribed_contacts: 12842,
  },
};

export const EMAIL_EMPTY: EmailOverview = {
  sent: 0,
  delivered: 0,
  opened: 0,
  clicked: 0,
  failed: 0,
  series: [],
  recent_activity: [],
  totals: { connected_accounts: 0, active_automations: 0, templates: 0, contacts: 0, subscribed_contacts: 0 },
};

export const CAMPAIGN: CampaignAnalytics = {
  recipients: 12842,
  delivered: 12581,
  opened: 5402,
  clicked: 1108,
  unsubscribed: 84,
  bounced: 261,
  complaints: 3,
  open_rate: 0.421,
  click_rate: 0.086,
  by_device: [
    { device: "desktop", opens: 2825 },
    { device: "mobile", opens: 2274 },
    { device: "tablet", opens: 303 },
  ],
  by_link: [
    { url: "https://shop.example.com/sale?utm_source=mail&token=secret", clicks: 423 },
    { url: "https://shop.example.com/shipping", clicks: 312 },
  ],
  series: [
    { date: "2026-09-20", opens: 2100, clicks: 420 },
    { date: "2026-09-21", opens: 1300, clicks: 300 },
  ],
  tracking: { opens: true, clicks: true },
};

export const HUB: HubOverview = {
  channels: [
    { key: "whatsapp", connected: true, detail: "+91 ••••• 4321 · Quality green" },
    { key: "email", connected: false, detail: null },
    { key: "sms", connected: false, detail: null },
    { key: "push", connected: false, detail: null },
  ],
  whatsapp: WHATSAPP,
  email: null,
  costs: {
    total: { amount_minor: 384250, currency: "INR" },
    estimated: true,
    by_channel: [
      { channel: "whatsapp", amount: { amount_minor: 384250, currency: "INR" } },
      { channel: "email", amount: { amount_minor: 0, currency: "INR" } },
    ],
    rate_card: { source: "platform_default", placeholder: true },
  },
};
