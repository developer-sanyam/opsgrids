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
 * In fixture mode (dev only, `VITE_USE_FIXTURES=true`) every read answers from
 * `analytics.fixtures.ts` in the shape the service sends, so the dashboards can be walked without
 * a gateway. Never reached live: `USE_FIXTURES` compiles to `false` in a production build.
 */

import { apiRequest } from "@opsgrids/client";

import {
  sampleCampaignAnalytics,
  sampleEmailOverview,
  sampleHubOverview,
  sampleWhatsAppOverview,
} from "./analytics.fixtures";
import { USE_FIXTURES, settle } from "./client";
export type {
  DateRange,
  Money,
  WhatsAppCategory,
  WhatsAppSeriesPoint,
  WhatsAppStatusCount,
  FailureReason,
  RecentActivity,
  WhatsAppTotals,
  CostByCategory,
  CostBySource,
  MessagingCost,
  WhatsAppOverview,
  EmailActivityKind,
  EmailSeriesPoint,
  EmailTotals,
  EmailOverview,
  CampaignAnalytics,
  AbVariant,
  AbTest,
  ChannelKey,
  HubChannel,
  RateCardRef,
  HubCosts,
  HubOverview,
} from "./analytics.types";
import type {
  DateRange,
  WhatsAppOverview,
  EmailOverview,
  CampaignAnalytics,
  HubOverview,
} from "./analytics.types";

/* -------------------------------- requests -------------------------------- */

function rangeQuery(range: DateRange): string {
  return new URLSearchParams({ from: range.from, to: range.to }).toString();
}

/** The WhatsApp overview and the deeper analytics page read the same endpoint. */
export async function getWhatsAppOverview(range: DateRange): Promise<WhatsAppOverview> {
  if (USE_FIXTURES) return settle(sampleWhatsAppOverview(range));
  return apiRequest<WhatsAppOverview>(`/engage/analytics/whatsapp/overview?${rangeQuery(range)}`);
}

export async function getEmailOverview(range: DateRange): Promise<EmailOverview> {
  if (USE_FIXTURES) return settle(sampleEmailOverview(range));
  return apiRequest<EmailOverview>(`/engage/analytics/email/overview?${rangeQuery(range)}`);
}

export async function getHubOverview(range: DateRange): Promise<HubOverview> {
  if (USE_FIXTURES) return settle(sampleHubOverview(range));
  return apiRequest<HubOverview>(`/engage/analytics/overview?${rangeQuery(range)}`);
}

export async function getCampaignAnalytics(campaignId: string): Promise<CampaignAnalytics> {
  if (USE_FIXTURES) return settle(sampleCampaignAnalytics());
  return apiRequest<CampaignAnalytics>(
    `/engage/campaigns/${encodeURIComponent(campaignId)}/analytics`,
  );
}