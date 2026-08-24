/**
 * Pure rollups over the campaign list. No I/O, no React — just grouping, sums and ratios, so the
 * Overview KPIs, the channel breakdown and the Channels table all derive from one
 * `getCampaigns()` payload. ROAS / CPL / CAC are computed here and nowhere else, so a rounding or
 * divide-by-zero choice lives in exactly one place. Money stays in integer minor units throughout.
 */

import type {
  Campaign,
  Channel,
  ChannelPerformance,
} from "@/api/types";

/** revenue / spend as a plain ratio; 0 when there is no spend (avoids divide-by-zero). */
export function roas(revenueMinor: number, spendMinor: number): number {
  return spendMinor === 0 ? 0 : revenueMinor / spendMinor;
}

/** spend / leads in minor units; null when there are no leads. */
export function costPerLead(spendMinor: number, leads: number): number | null {
  return leads === 0 ? null : Math.round(spendMinor / leads);
}

/** spend / conversions in minor units; null when nothing converted. */
export function cac(spendMinor: number, conversions: number): number | null {
  return conversions === 0 ? null : Math.round(spendMinor / conversions);
}

export interface OverviewTotals {
  revenueMinor: number;
  spendMinor: number;
  pipelineMinor: number;
  leads: number;
  conversions: number;
  cplMinor: number | null;
  roas: number;
}

/** The org-wide snapshot behind the four Overview KPI headlines. */
export function overviewTotals(campaigns: readonly Campaign[]): OverviewTotals {
  const revenueMinor = sum(campaigns, (c) => c.revenue_minor);
  const spendMinor = sum(campaigns, (c) => c.spent_minor);
  const pipelineMinor = sum(campaigns, (c) => c.pipeline_minor);
  const leads = sum(campaigns, (c) => c.leads);
  const conversions = sum(campaigns, (c) => c.conversions);
  return {
    revenueMinor,
    spendMinor,
    pipelineMinor,
    leads,
    conversions,
    cplMinor: costPerLead(spendMinor, leads),
    roas: roas(revenueMinor, spendMinor),
  };
}

/** Stable presentation order so the four channels never reshuffle between renders. */
export const CHANNEL_ORDER: Channel[] = ["search", "social", "email", "events"];

/**
 * Per-channel performance, one row per channel that has at least one campaign, largest revenue
 * first. Draft campaigns (no spend, no revenue) still count toward the campaign tally.
 */
export function channelPerformance(
  campaigns: readonly Campaign[],
): ChannelPerformance[] {
  return CHANNEL_ORDER.map((channel) => {
    const rows = campaigns.filter((c) => c.channel === channel);
    const spend_minor = sum(rows, (c) => c.spent_minor);
    const revenue_minor = sum(rows, (c) => c.revenue_minor);
    const pipeline_minor = sum(rows, (c) => c.pipeline_minor);
    const leads = sum(rows, (c) => c.leads);
    const conversions = sum(rows, (c) => c.conversions);
    return {
      channel,
      campaigns: rows.length,
      spend_minor,
      revenue_minor,
      pipeline_minor,
      leads,
      conversions,
      roas: roas(revenue_minor, spend_minor),
      cac_minor: cac(spend_minor, conversions),
      cpl_minor: costPerLead(spend_minor, leads),
    };
  })
    .filter((r) => r.campaigns > 0)
    .sort((a, b) => b.revenue_minor - a.revenue_minor);
}

export interface AttentionItem {
  campaign: Campaign;
  campaignRoas: number;
  /** Why this campaign needs a look. */
  reason: string;
}

/**
 * Live or paused campaigns that are spending but under-returning: a ROAS below 2.0 (barely
 * breaking even after cost of sale), or budget nearly exhausted with a ROAS still under 2.5.
 * Worst ROAS first. Draft and ended campaigns are excluded — there is nothing to act on.
 */
export function needsAttention(campaigns: readonly Campaign[]): AttentionItem[] {
  const items: AttentionItem[] = [];
  for (const campaign of campaigns) {
    if (campaign.status !== "live" && campaign.status !== "paused") continue;
    if (campaign.spent_minor === 0) continue;
    const r = roas(campaign.revenue_minor, campaign.spent_minor);
    const spendPace = campaign.spent_minor / campaign.budget_minor;
    let reason: string | null = null;
    if (r < 2) {
      reason = `ROAS ${r.toFixed(1)}× — below the 2× floor`;
    } else if (spendPace >= 0.9 && r < 2.5) {
      reason = `${Math.round(spendPace * 100)}% of budget spent at ${r.toFixed(1)}× ROAS`;
    }
    if (reason) items.push({ campaign, campaignRoas: r, reason });
  }
  return items.sort((a, b) => a.campaignRoas - b.campaignRoas);
}

/** How many campaigns target a given audience — the "used by N campaigns" cross-link. */
export function campaignsForAudience(
  campaigns: readonly Campaign[],
  audienceId: string,
): Campaign[] {
  return campaigns.filter((c) => c.audience_id === audienceId);
}

function sum<T>(rows: readonly T[], get: (row: T) => number): number {
  let total = 0;
  for (const row of rows) total += get(row);
  return total;
}
