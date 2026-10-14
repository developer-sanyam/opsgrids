/** Test-only: typed to the C1 contract so a change to the wire shape fails the typecheck here. */
import type { Customer360 } from "./customer360.types";

export const CUSTOMER_ID = "c1a0f2e4-9b3d-4a7c-8e1f-2d6b9c4a5e10";

export const CUSTOMER_360: Customer360 = {
  customer: {
    id: CUSTOMER_ID,
    name: "Priya Raghavan",
    company: "Vaayu Foods",
    since: "2025-03-14T00:00:00Z",
    status: "active",
    tags: ["Wholesale", "VIP"],
    owner: { id: "m-1", name: "Asha Menon" },
    phone: "•••••• 4210",
    email: "p•••••@vaayu.example.com",
    city: "Bengaluru",
  },
  crm: { stage: "Negotiation", open_opportunities: 2, hot: true },
  orders: {
    count_90d: 5,
    total_90d: { amount_minor: 1250000, currency: "INR" },
    latest: {
      number: "SO-1042",
      status: "shipped",
      total: { amount_minor: 250000, currency: "INR" },
      at: "2026-09-20T10:00:00Z",
    },
  },
  finance: {
    outstanding: { amount_minor: 480000, currency: "INR" },
    overdue_count: 1,
    latest_invoice: { number: "INV-2231", status: "overdue", due_on: "2026-09-10" },
  },
  support: { open_tickets: 1, total_tickets: 4 },
  channels: {
    whatsapp: {
      opted_in: true,
      window_open_until: new Date(Date.now() + 18 * 60 * 60 * 1000).toISOString(),
      last_message_at: "2026-09-25T10:30:00Z",
      conversations: 2,
    },
    email: { subscribed: true, last_email_at: "2026-09-18T08:00:00Z" },
  },
  timeline: [],
  unavailable: [],
};
