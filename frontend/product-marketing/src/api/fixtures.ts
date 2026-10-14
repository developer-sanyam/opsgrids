/**
 * Realistic fixture data, shaped exactly like the real API responses so the whole UI — including
 * every write — renders and behaves without a live backend. Swapped in by the `USE_FIXTURES` flag
 * in `client.ts`, dev-only.
 *
 * Money is minor units (paise). `lakh()` converts a figure in lakh of rupees to paise so the
 * seeds below stay readable: 1 lakh = ₹1,00,000 = 10,000,000 paise.
 *
 * The write functions below mirror the backend's own rules (`services/marketing/app/services.py`,
 * `app/domain.py`) closely enough that a screen built against fixtures behaves the same as it will
 * live: a duplicate audience name 409s, a campaign needs a real audience in the same "org", a
 * status move outside `CAMPAIGN_TRANSITIONS` 409s, and an audience still targeted by a campaign
 * refuses to delete.
 */

import { ApiError, paginateFixture as paginate } from "@opsgrids/client";

import { channelPerformance, overviewTotals } from "@/data/derive";
import type {
  Audience,
  AudienceIn,
  AudiencePatch,
  Campaign,
  CampaignIn,
  CampaignPatch,
  CampaignStatus,
  Channel,
  ChannelPerformance,
  OverviewSummary,
  Page,
  RevenueSpendPoint,
} from "./types";

const CURRENCY = "INR";

/** The date the fixture seed was written against — every "today" fixture-mode reads. */
export const FIXTURE_TODAY = "2026-08-15";

/** Lakh of rupees → paise (minor units). */
const lakh = (n: number): number => Math.round(n * 10_000_000);

function notFound(message: string): ApiError {
  return new ApiError(404, message, { status: 404, title: "Not found", detail: message });
}

function conflict(message: string): ApiError {
  return new ApiError(409, message, { status: 409, title: "Conflict", detail: message });
}

function businessRule(field: string, message: string): ApiError {
  return new ApiError(422, message, {
    status: 422,
    title: "Business rule violation",
    detail: message,
    errors: [{ field, code: "value_error", message }],
  });
}

function fixtureId(prefix: string): string {
  return `${prefix}-${crypto.randomUUID()}`;
}


/** The lifecycle graph — a byte-for-byte mirror of `app.domain.CAMPAIGN_TRANSITIONS`. */
const CAMPAIGN_TRANSITIONS: Record<CampaignStatus, ReadonlySet<CampaignStatus>> = {
  draft: new Set(["live"]),
  live: new Set(["paused", "ended"]),
  paused: new Set(["live", "ended"]),
  ended: new Set(),
};

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

export interface ListAudiencesParams {
  q?: string;
  page?: number;
  size?: number;
}

function matchesAudienceQuery(a: Audience, q: string): boolean {
  if (q === "") return true;
  return a.name.toLowerCase().includes(q) || a.criteria.toLowerCase().includes(q);
}

export function listAudiencesFixture(params: ListAudiencesParams = {}): Page<Audience> {
  const page = params.page ?? 1;
  const size = params.size ?? 25;
  const q = (params.q ?? "").trim().toLowerCase();
  const sorted = AUDIENCES.filter((a) => matchesAudienceQuery(a, q)).sort((a, b) =>
    a.name.localeCompare(b.name),
  );
  return paginate(sorted, page, size);
}

function findAudienceByName(name: string, excludeId?: string): Audience | undefined {
  return AUDIENCES.find(
    (a) => a.id !== excludeId && a.name.toLowerCase() === name.toLowerCase(),
  );
}

export function createAudienceFixture(input: AudienceIn): Audience {
  if (findAudienceByName(input.name)) {
    throw conflict(`An audience named '${input.name}' already exists`);
  }
  const audience: Audience = {
    id: fixtureId("aud"),
    name: input.name,
    size: input.size,
    criteria: input.criteria ?? "",
    created_at: input.created_at,
  };
  AUDIENCES.unshift(audience);
  return audience;
}

export function updateAudienceFixture(id: string, patch: AudiencePatch): Audience {
  const index = AUDIENCES.findIndex((a) => a.id === id);
  const current = index === -1 ? undefined : AUDIENCES[index];
  if (!current) throw notFound("That audience is not part of this organization");
  if (patch.name !== undefined && patch.name !== current.name && findAudienceByName(patch.name, id)) {
    throw conflict(`An audience named '${patch.name}' already exists`);
  }
  const updated: Audience = {
    ...current,
    ...(patch.name !== undefined ? { name: patch.name } : {}),
    ...(patch.size !== undefined ? { size: patch.size } : {}),
    ...(patch.criteria !== undefined ? { criteria: patch.criteria } : {}),
    ...(patch.created_at !== undefined ? { created_at: patch.created_at } : {}),
  };
  AUDIENCES[index] = updated;
  return updated;
}

export function deleteAudienceFixture(id: string): void {
  const index = AUDIENCES.findIndex((a) => a.id === id);
  const current = index === -1 ? undefined : AUDIENCES[index];
  if (!current) throw notFound("That audience is not part of this organization");
  const inUse = CAMPAIGNS.filter((c) => c.audience_id === id).length;
  if (inUse > 0) {
    throw conflict(`Used by ${inUse} campaign${inUse === 1 ? "" : "s"} — retarget or delete them first`);
  }
  AUDIENCES.splice(index, 1);
}

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

export interface ListCampaignsParams {
  q?: string;
  channel?: Channel | "all";
  status?: CampaignStatus | "all";
  audience_id?: string;
  page?: number;
  size?: number;
}

function matchesCampaignFilters(c: Campaign, params: ListCampaignsParams): boolean {
  const q = (params.q ?? "").trim().toLowerCase();
  const matchesQuery =
    q === "" ||
    c.name.toLowerCase().includes(q) ||
    c.goal.toLowerCase().includes(q) ||
    c.owner.toLowerCase().includes(q);
  const matchesChannel = !params.channel || params.channel === "all" || c.channel === params.channel;
  const matchesStatus = !params.status || params.status === "all" || c.status === params.status;
  const matchesAudience = !params.audience_id || c.audience_id === params.audience_id;
  return matchesQuery && matchesChannel && matchesStatus && matchesAudience;
}

export function listCampaignsFixture(params: ListCampaignsParams = {}): Page<Campaign> {
  const page = params.page ?? 1;
  const size = params.size ?? 25;
  const sorted = CAMPAIGNS.filter((c) => matchesCampaignFilters(c, params)).sort((a, b) =>
    a.name.localeCompare(b.name),
  );
  return paginate(sorted, page, size);
}

export function getCampaignFixture(id: string): Campaign {
  const found = CAMPAIGNS.find((c) => c.id === id);
  if (!found) throw notFound("That campaign is not part of this organization");
  return found;
}

export function createCampaignFixture(input: CampaignIn): Campaign {
  const audience = AUDIENCES.find((a) => a.id === input.audience_id);
  if (!audience) throw notFound("That audience is not part of this organization");
  if (input.end_date && input.end_date < input.start_date) {
    throw businessRule("end_date", "end_date must be on or after start_date");
  }
  const campaign: Campaign = {
    id: fixtureId("cmp"),
    name: input.name,
    channel: input.channel,
    status: input.status ?? "draft",
    currency: (input.currency ?? "INR").toUpperCase(),
    budget_minor: input.budget_minor ?? 0,
    spent_minor: input.spent_minor ?? 0,
    revenue_minor: input.revenue_minor ?? 0,
    pipeline_minor: input.pipeline_minor ?? 0,
    leads: input.leads ?? 0,
    conversions: input.conversions ?? 0,
    goal: input.goal ?? "",
    audience_id: input.audience_id,
    start_date: input.start_date,
    end_date: input.end_date ?? null,
    owner: input.owner ?? "",
  };
  CAMPAIGNS.unshift(campaign);
  return campaign;
}

export function updateCampaignFixture(id: string, patch: CampaignPatch): Campaign {
  const index = CAMPAIGNS.findIndex((c) => c.id === id);
  const current = index === -1 ? undefined : CAMPAIGNS[index];
  if (!current) throw notFound("That campaign is not part of this organization");

  if (patch.audience_id !== undefined && !AUDIENCES.some((a) => a.id === patch.audience_id)) {
    throw notFound("That audience is not part of this organization");
  }
  if (patch.status !== undefined && patch.status !== current.status) {
    const allowed = CAMPAIGN_TRANSITIONS[current.status];
    if (!allowed.has(patch.status)) {
      throw conflict(`A campaign cannot move from '${current.status}' to '${patch.status}'`);
    }
  }
  const mergedStart = patch.start_date ?? current.start_date;
  const mergedEnd = patch.end_date !== undefined ? patch.end_date : current.end_date;
  if (mergedEnd !== null && mergedEnd !== undefined && mergedEnd < mergedStart) {
    throw businessRule("end_date", "end_date must be on or after start_date");
  }

  const updated: Campaign = {
    ...current,
    ...(patch.name !== undefined ? { name: patch.name } : {}),
    ...(patch.channel !== undefined ? { channel: patch.channel } : {}),
    ...(patch.status !== undefined ? { status: patch.status } : {}),
    ...(patch.budget_minor !== undefined ? { budget_minor: patch.budget_minor } : {}),
    ...(patch.spent_minor !== undefined ? { spent_minor: patch.spent_minor } : {}),
    ...(patch.revenue_minor !== undefined ? { revenue_minor: patch.revenue_minor } : {}),
    ...(patch.pipeline_minor !== undefined ? { pipeline_minor: patch.pipeline_minor } : {}),
    ...(patch.leads !== undefined ? { leads: patch.leads } : {}),
    ...(patch.conversions !== undefined ? { conversions: patch.conversions } : {}),
    ...(patch.goal !== undefined ? { goal: patch.goal } : {}),
    ...(patch.audience_id !== undefined ? { audience_id: patch.audience_id } : {}),
    ...(patch.start_date !== undefined ? { start_date: patch.start_date } : {}),
    ...(patch.end_date !== undefined ? { end_date: patch.end_date } : {}),
    ...(patch.owner !== undefined ? { owner: patch.owner } : {}),
  };
  CAMPAIGNS[index] = updated;
  return updated;
}

export function deleteCampaignFixture(id: string): void {
  const index = CAMPAIGNS.findIndex((c) => c.id === id);
  if (index === -1) throw notFound("That campaign is not part of this organization");
  CAMPAIGNS.splice(index, 1);
}

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
 * fraction of that campaign's totals so it scales to any campaign. Eight flight-weeks — the exact
 * shape `app.domain.CAMPAIGN_WEEK_SHAPE` uses live.
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

/* ------------------------------ rollups ----------------------------------- */

export function channelsFixture(): ChannelPerformance[] {
  return channelPerformance(CAMPAIGNS);
}

export function overviewFixture(): OverviewSummary {
  const totals = overviewTotals(CAMPAIGNS);
  const currency = CAMPAIGNS[0]?.currency ?? "INR";
  return {
    currency,
    revenue_minor: totals.revenueMinor,
    spend_minor: totals.spendMinor,
    pipeline_minor: totals.pipelineMinor,
    leads: totals.leads,
    conversions: totals.conversions,
    roas: totals.roas,
    cpl_minor: totals.cplMinor,
    revenue_vs_spend: REVENUE_SPEND_SERIES,
    channels: channelPerformance(CAMPAIGNS),
  };
}
