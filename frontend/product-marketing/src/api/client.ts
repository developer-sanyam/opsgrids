/**
 * Typed Marketing API client.
 *
 * Live by default: every function calls the marketing service through the gateway at the
 * same-origin `/api/v1/marketing/*` prefix (Vite proxies it to the gateway in dev) with the
 * bearer token from the session. Set `VITE_USE_FIXTURES=true` to render the whole app from
 * `fixtures.ts` with no login — handy for offline design work; fixture writes mutate the
 * in-memory fixture arrays so the screen behaves the same as live, and the flag is gated on
 * `import.meta.env.DEV` so it can never ship in a production build.
 *
 * Every list function takes the server's own filters and paging and passes them straight
 * through — nothing here re-filters, re-sorts or caps a result set the server already decided.
 * The service exposes no `sort` parameter on any list (fixed `name` order), so none is offered
 * here either — faking a client-side sort over one page of a server-paged list would silently
 * misorder every row the current page does not hold.
 */

import { apiRequest, buildQuery, settle } from "@opsgrids/client";
import {
  CAMPAIGNS,
  CAMPAIGN_WEEK_SHAPE,
  createAudienceFixture,
  createCampaignFixture,
  deleteAudienceFixture,
  deleteCampaignFixture,
  getCampaignFixture,
  listAudiencesFixture,
  listCampaignsFixture,
  overviewFixture,
  channelsFixture,
  updateAudienceFixture,
  updateCampaignFixture,
} from "./fixtures";
import { ApiError } from "@opsgrids/client";
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

/** Default source. Live unless `VITE_USE_FIXTURES=true` pins the app to fixtures.
 *  Dev builds only: `import.meta.env.DEV` compiles to `false` in production, so a stray
 *  CI/production env var can never ship fixture mode — an app rendering fabricated data
 *  with the "Sample data" markers suppressed. */
export const USE_FIXTURES = import.meta.env.DEV && import.meta.env.VITE_USE_FIXTURES === "true";

/** The real default page size for a paginated list screen. */
export const DEFAULT_PAGE_SIZE = 25;
/** The server's own ceiling (`PageQuery`, `libs/platform-core/src/platform_core/pagination.py`) —
 *  used only for a bounded working set a form's picker needs in full, never for a list screen. */
export const MAX_PAGE_SIZE = 200;

/* -------------------------------- audiences ------------------------------- */

export interface ListAudiencesParams {
  q?: string;
  page?: number;
  size?: number;
}

/** Every audience segment for this org, paged and searched server-side. */
export async function getAudiences(params: ListAudiencesParams = {}): Promise<Page<Audience>> {
  const page = params.page ?? 1;
  const size = params.size ?? DEFAULT_PAGE_SIZE;
  if (USE_FIXTURES) return settle(listAudiencesFixture({ ...params, page, size }));
  const query = buildQuery({ page, size, q: params.q });
  return apiRequest<Page<Audience>>(`/marketing/audiences${query}`);
}

/** `POST /marketing/audiences` — `marketing.analytics.campaign.write`. 409 on a duplicate name. */
export async function createAudience(input: AudienceIn): Promise<Audience> {
  if (USE_FIXTURES) return settle(createAudienceFixture(input));
  return apiRequest<Audience>("/marketing/audiences", { method: "POST", body: input });
}

/** `PATCH /marketing/audiences/{id}` — same permission. Renaming re-checks uniqueness → 409. */
export async function updateAudience(id: string, patch: AudiencePatch): Promise<Audience> {
  if (USE_FIXTURES) return settle(updateAudienceFixture(id, patch));
  return apiRequest<Audience>(`/marketing/audiences/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: patch,
  });
}

/** `DELETE /marketing/audiences/{id}` → 204. 409 while any live campaign still targets it. */
export async function deleteAudience(id: string): Promise<void> {
  if (USE_FIXTURES) {
    deleteAudienceFixture(id);
    return settle(undefined);
  }
  await apiRequest<undefined>(`/marketing/audiences/${encodeURIComponent(id)}`, { method: "DELETE" });
}

/* -------------------------------- campaigns ------------------------------- */

export interface ListCampaignsParams {
  q?: string;
  channel?: Channel | "all";
  status?: CampaignStatus | "all";
  audience_id?: string;
  page?: number;
  size?: number;
}

/** Campaigns for this org, paged and filtered server-side. */
export async function getCampaigns(params: ListCampaignsParams = {}): Promise<Page<Campaign>> {
  const page = params.page ?? 1;
  const size = params.size ?? DEFAULT_PAGE_SIZE;
  if (USE_FIXTURES) return settle(listCampaignsFixture({ ...params, page, size }));
  const query = buildQuery({
    page,
    size,
    q: params.q,
    channel: params.channel === "all" ? undefined : params.channel,
    status: params.status === "all" ? undefined : params.status,
    audience_id: params.audience_id,
  });
  return apiRequest<Page<Campaign>>(`/marketing/campaigns${query}`);
}

/** One campaign by id. Throws a 404 `ApiError` when it is not found (live and fixtures alike). */
export async function getCampaign(id: string): Promise<Campaign> {
  if (USE_FIXTURES) return settle(getCampaignFixture(id));
  return apiRequest<Campaign>(`/marketing/campaigns/${encodeURIComponent(id)}`);
}

/**
 * `POST /marketing/campaigns` — `marketing.analytics.campaign.write`. `audience_id` is required;
 * naming another organization's audience — or one that does not exist — reads as a 404. A 422
 * means `end_date` fell before `start_date` (also validated client-side, before this is called).
 */
export async function createCampaign(input: CampaignIn): Promise<Campaign> {
  if (USE_FIXTURES) return settle(createCampaignFixture(input));
  return apiRequest<Campaign>("/marketing/campaigns", { method: "POST", body: input });
}

/**
 * `PATCH /marketing/campaigns/{id}` — same permission, and also how a status change is made:
 * `status` moves only through the domain's lifecycle transitions; an illegal move is a 409.
 * `currency` is not accepted here — it cannot change once a campaign exists.
 */
export async function updateCampaign(id: string, patch: CampaignPatch): Promise<Campaign> {
  if (USE_FIXTURES) return settle(updateCampaignFixture(id, patch));
  return apiRequest<Campaign>(`/marketing/campaigns/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: patch,
  });
}

/** `DELETE /marketing/campaigns/{id}` → 204, soft delete. */
export async function deleteCampaign(id: string): Promise<void> {
  if (USE_FIXTURES) {
    deleteCampaignFixture(id);
    return settle(undefined);
  }
  await apiRequest<undefined>(`/marketing/campaigns/${encodeURIComponent(id)}`, { method: "DELETE" });
}

/**
 * A campaign's weekly revenue-and-spend series for its detail chart. In fixtures this distributes
 * the campaign's totals across its flight-weeks with a stable shape; live, an analytics endpoint
 * returns the real weekly split.
 */
export async function getCampaignWeekly(id: string): Promise<RevenueSpendPoint[]> {
  if (USE_FIXTURES) {
    const c = CAMPAIGNS.find((x) => x.id === id);
    if (!c) {
      throw new ApiError(404, "That campaign no longer exists.", {
        status: 404,
        title: "Not found",
      });
    }
    return settle(
      CAMPAIGN_WEEK_SHAPE.map((w) => ({
        label: w.label,
        revenue_minor: Math.round(c.revenue_minor * w.revenue),
        spend_minor: Math.round(c.spent_minor * w.spend),
      })),
    );
  }
  return apiRequest<RevenueSpendPoint[]>(
    `/marketing/campaigns/${encodeURIComponent(id)}/performance`,
  );
}

/* -------------------------------- analytics -------------------------------- */

/** Per-channel performance, rolled up server-side from every campaign in scope — never just the
 *  Channels table's own first page. */
export async function getChannels(): Promise<ChannelPerformance[]> {
  if (USE_FIXTURES) return settle(channelsFixture());
  return apiRequest<ChannelPerformance[]>(`/marketing/channels`);
}

/** The Overview screen in one payload — the four KPI headlines, the revenue-vs-spend series and
 *  the channel breakdown, all computed server-side over the org's full campaign set so the
 *  numbers can never silently disagree with a client-paged list. */
export async function getOverview(): Promise<OverviewSummary> {
  if (USE_FIXTURES) return settle(overviewFixture());
  return apiRequest<OverviewSummary>(`/marketing/overview`);
}

/** A small cap search for the campaign form's audience picker, and for checking whether the org
 *  has any audience at all. Audience counts are small; a single bounded page covers them. */
export async function listAudiencesForPicker(): Promise<Audience[]> {
  const page = await getAudiences({ size: MAX_PAGE_SIZE });
  return page.items;
}
