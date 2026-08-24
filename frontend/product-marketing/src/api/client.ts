/**
 * Typed Marketing API client.
 *
 * **Fixtures by default.** The marketing service is still a skeleton on this branch, so the whole
 * app renders from `fixtures.ts` with no login. Once the service ships, set
 * `VITE_USE_FIXTURES=false` and every function below calls the gateway at the same-origin
 * `/api/v1/marketing/*` prefix (Vite proxies it to `http://localhost:8100` in dev) with the bearer
 * token from the session — wiring the product to live data is a single flag flip.
 */

import { apiRequest } from "./http";
import {
  AUDIENCES,
  CAMPAIGNS,
  CAMPAIGN_WEEK_SHAPE,
  REVENUE_SPEND_SERIES,
} from "./fixtures";
import { ApiError } from "./http";
import type { Audience, Campaign, Page, RevenueSpendPoint } from "./types";

/** Fixtures unless `VITE_USE_FIXTURES=false` explicitly points the app at the live gateway. */
export const USE_FIXTURES = import.meta.env.VITE_USE_FIXTURES !== "false";

/** True when the app is talking to the real gateway. */
export const LIVE = !USE_FIXTURES;

/** A generous page size — one request returns the whole (small) working set for this org. */
const FULL_PAGE = 200;

/** Small artificial delay so loading states are exercised in fixture mode. */
function settle<T>(value: T, ms = 240): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(value), ms));
}

/** Every campaign for this org — the Campaigns table, Channels rollup and Overview derive from it. */
export async function getCampaigns(): Promise<Page<Campaign>> {
  if (USE_FIXTURES) return settle({ items: CAMPAIGNS, total: CAMPAIGNS.length });
  return apiRequest<Page<Campaign>>(`/marketing/campaigns?size=${FULL_PAGE}`);
}

/** One campaign by id. Throws a 404 `ApiError` when it is not found (live and fixtures alike). */
export async function getCampaign(id: string): Promise<Campaign> {
  if (USE_FIXTURES) {
    const found = CAMPAIGNS.find((c) => c.id === id);
    if (!found) {
      throw new ApiError(404, "That campaign no longer exists.", {
        status: 404,
        title: "Not found",
      });
    }
    return settle(found);
  }
  return apiRequest<Campaign>(`/marketing/campaigns/${encodeURIComponent(id)}`);
}

/** Every audience segment for this org. */
export async function getAudiences(): Promise<Page<Audience>> {
  if (USE_FIXTURES) return settle({ items: AUDIENCES, total: AUDIENCES.length });
  return apiRequest<Page<Audience>>(`/marketing/audiences?size=${FULL_PAGE}`);
}

/** The revenue-vs-spend time series behind the Overview chart. */
export async function getRevenueSpendSeries(): Promise<RevenueSpendPoint[]> {
  if (USE_FIXTURES) return settle(REVENUE_SPEND_SERIES);
  return apiRequest<RevenueSpendPoint[]>(`/marketing/analytics/revenue-vs-spend`);
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
