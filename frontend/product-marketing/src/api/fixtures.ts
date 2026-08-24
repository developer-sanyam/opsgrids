/**
 * Realistic fixture data, shaped exactly like the real API responses so the whole UI renders
 * without a live backend. Swapped out by the `USE_FIXTURES` flag in `client.ts`.
 *
 * Money is minor units (paise). `lakh()` converts a figure in lakh of rupees to paise so the
 * seeds below stay readable: 1 lakh = ₹1,00,000 = 10,000,000 paise.
 */

import type { Audience, Campaign, Channel, RevenueSpendPoint } from "./types";

const CURRENCY = "INR";

/** Lakh of rupees → paise (minor units). */
const lakh = (n: number): number => Math.round(n * 10_000_000);

/* ------------------------------- audiences ------------------------------- */

interface AudienceSeed {
  id: string;
  name: string;
  size: number;
  criteria: string;
  created_at: string;
}

const AUDIENCE_SEEDS: AudienceSeed[] = [
  {
    id: "aud-ent-india",
    name: "Enterprise India",
    size: 4210,
    criteria: "1000+ employees · India HQ · IT, BFSI & manufacturing",
    created_at: "2025-11-02",
  },
  {
    id: "aud-growth-saas",
    name: "Growth-stage SaaS",
    size: 9840,
    criteria: "SaaS · Series A–C · 50–500 staff · product-led",
    created_at: "2025-09-18",
  },
  {
    id: "aud-trial-lapsed",
    name: "Lapsed trials",
    size: 3155,
    criteria: "Trial ended 30–90 days ago · no purchase · opened ≥1 email",
    created_at: "2026-01-12",
  },
  {
    id: "aud-in-market",
    name: "In-market buyers",
    size: 15620,
    criteria: "Pricing-page visit · repeat sessions · high intent score",
    created_at: "2025-08-04",
  },
  {
    id: "aud-existing-cust",
    name: "Existing customers",
    size: 2470,
    criteria: "Active subscription · health score > 60 · expansion-ready",
    created_at: "2025-07-21",
  },
  {
    id: "aud-gcc-mid",
    name: "GCC mid-market",
    size: 6090,
    criteria: "UAE & KSA HQ · 200–2000 staff · English-first buyers",
    created_at: "2026-02-09",
  },
];

export const AUDIENCES: Audience[] = AUDIENCE_SEEDS.map((a) => ({ ...a }));

/* ------------------------------- campaigns ------------------------------- */

interface CampaignSeed {
  id: string;
  name: string;
  channel: Channel;
  status: Campaign["status"];
  /** All money in lakh of rupees; converted to minor units below. */
  budget: number;
  spent: number;
  revenue: number;
  pipeline: number;
  leads: number;
  conversions: number;
  goal: string;
  audience_id: string;
  start_date: string;
  end_date: string | null;
  owner: string;
}

const CAMPAIGN_SEEDS: CampaignSeed[] = [
  {
    id: "cmp-1001",
    name: "Diwali Enterprise Push",
    channel: "search",
    status: "live",
    budget: 32,
    spent: 28.4,
    revenue: 142,
    pipeline: 320,
    leads: 640,
    conversions: 34,
    goal: "Enterprise pipeline",
    audience_id: "aud-ent-india",
    start_date: "2026-06-15",
    end_date: null,
    owner: "Meghna Rao",
  },
  {
    id: "cmp-1002",
    name: "Q3 Webinar Series",
    channel: "events",
    status: "live",
    budget: 18,
    spent: 15.2,
    revenue: 61,
    pipeline: 180,
    leads: 410,
    conversions: 18,
    goal: "MQL volume",
    audience_id: "aud-growth-saas",
    start_date: "2026-07-01",
    end_date: null,
    owner: "Arre Farhan",
  },
  {
    id: "cmp-1003",
    name: "Retargeting: Abandoned Trials",
    channel: "social",
    status: "live",
    budget: 9,
    spent: 8.7,
    revenue: 47,
    pipeline: 66,
    leads: 520,
    conversions: 41,
    goal: "Trial reactivation",
    audience_id: "aud-trial-lapsed",
    start_date: "2026-05-20",
    end_date: null,
    owner: "Priya Nair",
  },
  {
    id: "cmp-1004",
    name: "Brand Always-On Search",
    channel: "search",
    status: "live",
    budget: 24,
    spent: 22.1,
    revenue: 98,
    pipeline: 210,
    leads: 880,
    conversions: 52,
    goal: "Brand demand capture",
    audience_id: "aud-in-market",
    start_date: "2026-01-05",
    end_date: null,
    owner: "Meghna Rao",
  },
  {
    id: "cmp-1005",
    name: "Founder Newsletter",
    channel: "email",
    status: "live",
    budget: 4,
    spent: 3.6,
    revenue: 39,
    pipeline: 54,
    leads: 300,
    conversions: 22,
    goal: "Nurture & recall",
    audience_id: "aud-existing-cust",
    start_date: "2026-02-01",
    end_date: null,
    owner: "Dev Sharma",
  },
  {
    id: "cmp-1006",
    name: "LinkedIn Thought Leadership",
    channel: "social",
    status: "paused",
    budget: 14,
    spent: 11.9,
    revenue: 22,
    pipeline: 88,
    leads: 240,
    conversions: 7,
    goal: "ABM awareness",
    audience_id: "aud-ent-india",
    start_date: "2026-04-10",
    end_date: null,
    owner: "Arre Farhan",
  },
  {
    id: "cmp-1007",
    name: "GCC Expansion Roadshow",
    channel: "events",
    status: "live",
    budget: 30,
    spent: 19.8,
    revenue: 74,
    pipeline: 260,
    leads: 180,
    conversions: 16,
    goal: "Field pipeline",
    audience_id: "aud-gcc-mid",
    start_date: "2026-07-12",
    end_date: null,
    owner: "Layla Hassan",
  },
  {
    id: "cmp-1008",
    name: "Cart Recovery Emails",
    channel: "email",
    status: "live",
    budget: 3,
    spent: 2.9,
    revenue: 51,
    pipeline: 30,
    leads: 410,
    conversions: 58,
    goal: "Recover revenue",
    audience_id: "aud-trial-lapsed",
    start_date: "2026-03-08",
    end_date: null,
    owner: "Dev Sharma",
  },
  {
    id: "cmp-1009",
    name: "Performance Max — Q2",
    channel: "search",
    status: "ended",
    budget: 26,
    spent: 26,
    revenue: 88,
    pipeline: 140,
    leads: 720,
    conversions: 44,
    goal: "Signup volume",
    audience_id: "aud-in-market",
    start_date: "2026-04-01",
    end_date: "2026-06-30",
    owner: "Meghna Rao",
  },
  {
    id: "cmp-1010",
    name: "Instagram Reels Test",
    channel: "social",
    status: "draft",
    budget: 6,
    spent: 0,
    revenue: 0,
    pipeline: 0,
    leads: 0,
    conversions: 0,
    goal: "Awareness experiment",
    audience_id: "aud-growth-saas",
    start_date: "2026-09-01",
    end_date: null,
    owner: "Priya Nair",
  },
  {
    id: "cmp-1011",
    name: "Partner Co-Marketing",
    channel: "events",
    status: "live",
    budget: 12,
    spent: 9.4,
    revenue: 58,
    pipeline: 120,
    leads: 150,
    conversions: 14,
    goal: "Partner-sourced leads",
    audience_id: "aud-gcc-mid",
    start_date: "2026-06-02",
    end_date: null,
    owner: "Layla Hassan",
  },
  {
    id: "cmp-1012",
    name: "Product Update Digest",
    channel: "email",
    status: "live",
    budget: 2.5,
    spent: 2.2,
    revenue: 28,
    pipeline: 24,
    leads: 260,
    conversions: 19,
    goal: "Expansion nudge",
    audience_id: "aud-existing-cust",
    start_date: "2026-01-20",
    end_date: null,
    owner: "Dev Sharma",
  },
  {
    id: "cmp-1013",
    name: "Competitor Conquest Search",
    channel: "search",
    status: "paused",
    budget: 16,
    spent: 15.6,
    revenue: 24,
    pipeline: 70,
    leads: 290,
    conversions: 6,
    goal: "Displacement",
    audience_id: "aud-in-market",
    start_date: "2026-05-01",
    end_date: null,
    owner: "Meghna Rao",
  },
  {
    id: "cmp-1014",
    name: "Analyst Summit 2026",
    channel: "events",
    status: "ended",
    budget: 22,
    spent: 21.4,
    revenue: 96,
    pipeline: 240,
    leads: 130,
    conversions: 21,
    goal: "Enterprise trust",
    audience_id: "aud-ent-india",
    start_date: "2026-02-18",
    end_date: "2026-03-31",
    owner: "Arre Farhan",
  },
];

export const CAMPAIGNS: Campaign[] = CAMPAIGN_SEEDS.map((s) => ({
  id: s.id,
  name: s.name,
  channel: s.channel,
  status: s.status,
  currency: CURRENCY,
  budget_minor: lakh(s.budget),
  spent_minor: lakh(s.spent),
  revenue_minor: lakh(s.revenue),
  pipeline_minor: lakh(s.pipeline),
  leads: s.leads,
  conversions: s.conversions,
  goal: s.goal,
  audience_id: s.audience_id,
  start_date: s.start_date,
  end_date: s.end_date,
  owner: s.owner,
}));

/* ------------------------ revenue vs spend, monthly ----------------------- */

interface SeriesSeed {
  label: string;
  revenue: number;
  spend: number;
}

// Eight months of attributed revenue against spend (lakh of rupees). Spend climbs steadily;
// revenue climbs faster — the story this product exists to tell.
const SERIES_SEED: SeriesSeed[] = [
  { label: "Jan", revenue: 172, spend: 58 },
  { label: "Feb", revenue: 190, spend: 62 },
  { label: "Mar", revenue: 205, spend: 71 },
  { label: "Apr", revenue: 198, spend: 66 },
  { label: "May", revenue: 244, spend: 78 },
  { label: "Jun", revenue: 270, spend: 84 },
  { label: "Jul", revenue: 312, spend: 92 },
  { label: "Aug", revenue: 336, spend: 88 },
];

export const REVENUE_SPEND_SERIES: RevenueSpendPoint[] = SERIES_SEED.map((p) => ({
  label: p.label,
  revenue_minor: lakh(p.revenue),
  spend_minor: lakh(p.spend),
}));

/**
 * A representative weekly performance series for a single campaign detail screen, expressed as a
 * fraction of that campaign's totals so it scales to any campaign. Eight flight-weeks.
 */
export const CAMPAIGN_WEEK_SHAPE: { label: string; revenue: number; spend: number }[] = [
  { label: "W1", revenue: 0.04, spend: 0.09 },
  { label: "W2", revenue: 0.08, spend: 0.12 },
  { label: "W3", revenue: 0.11, spend: 0.13 },
  { label: "W4", revenue: 0.14, spend: 0.14 },
  { label: "W5", revenue: 0.15, spend: 0.13 },
  { label: "W6", revenue: 0.16, spend: 0.12 },
  { label: "W7", revenue: 0.16, spend: 0.14 },
  { label: "W8", revenue: 0.16, spend: 0.13 },
];
