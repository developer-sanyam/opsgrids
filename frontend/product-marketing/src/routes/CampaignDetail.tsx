import { useCallback } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import {
  getAudiences,
  getCampaign,
  getCampaigns,
  getCampaignWeekly,
} from "@/api/client";
import { cac, campaignsForAudience, costPerLead, roas } from "@/data/derive";
import { useAsync } from "@/lib/useAsync";
import {
  channelLabel,
  formatDate,
  formatMoney,
  formatMoneyCompact,
  formatNumber,
  formatRoas,
} from "@/lib/format";
import {
  Button,
  Card,
  CampaignPerformanceChart,
  CampaignStatusPill,
  ChannelBadge,
  ErrorState,
  SectionHeader,
  Skeleton,
  StatTile,
} from "@/ui";
import { ArrowRightIcon, TargetIcon, UsersIcon } from "@/ui/icons";

export function CampaignDetailPage() {
  const { id = "" } = useParams();
  const navigate = useNavigate();

  const loader = useCallback(
    () => Promise.all([getCampaign(id), getAudiences(), getCampaigns(), getCampaignWeekly(id)]),
    [id],
  );
  const detail = useAsync(loader, [id]);

  if (detail.status === "loading") {
    return (
      <div className="rise flex flex-col gap-6">
        <Skeleton className="h-8 w-40 rounded-tile" />
        <Skeleton className="h-24 w-full rounded-card" />
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 xl:grid-cols-6">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-24 w-full rounded-tile" />
          ))}
        </div>
        <Skeleton className="h-72 w-full rounded-card" />
      </div>
    );
  }

  if (detail.status === "error") {
    return (
      <div className="rise flex flex-col gap-4">
        <BackLink />
        <Card className="p-8">
          <ErrorState
            message={`${detail.error.message} It may have been renamed or removed.`}
            onRetry={() => navigate("/campaigns")}
          />
        </Card>
      </div>
    );
  }

  const [campaign, audiencePage, campaignPage, weekly] = detail.data;
  const audience = audiencePage.items.find((a) => a.id === campaign.audience_id) ?? null;
  const usedBy = audience
    ? campaignsForAudience(campaignPage.items, audience.id).length
    : 0;

  const r = roas(campaign.revenue_minor, campaign.spent_minor);
  const cpl = costPerLead(campaign.spent_minor, campaign.leads);
  const acquisition = cac(campaign.spent_minor, campaign.conversions);
  const isDraft = campaign.status === "draft";

  return (
    <div className="rise flex flex-col gap-6">
      <BackLink />

      {/* Header */}
      <header className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div className="flex flex-col gap-3">
          <div className="flex flex-wrap items-center gap-2">
            <ChannelBadge channel={campaign.channel} />
            <CampaignStatusPill status={campaign.status} />
          </div>
          <h1 className="display text-3xl font-extrabold leading-tight sm:text-[2.1rem]">
            {campaign.name}
          </h1>
          <p className="text-sm text-muted">
            {channelLabel(campaign.channel)} · owned by{" "}
            <span className="font-semibold text-ink-soft">{campaign.owner}</span> ·{" "}
            {formatDate(campaign.start_date)} – {formatDate(campaign.end_date)}
          </p>
        </div>
        <Button variant="outline">Edit campaign</Button>
      </header>

      {/* Metric tiles */}
      <section
        aria-label="Campaign metrics"
        className="grid grid-cols-2 gap-4 sm:grid-cols-3 xl:grid-cols-6"
      >
        <StatTile
          label="Revenue"
          value={formatMoneyCompact(campaign.revenue_minor, campaign.currency)}
          sub="attributed"
        />
        <StatTile
          label="Pipeline"
          value={formatMoneyCompact(campaign.pipeline_minor, campaign.currency)}
          sub="open value"
        />
        <StatTile label="Leads" value={formatNumber(campaign.leads)} sub="qualified" />
        <StatTile label="Deals" value={formatNumber(campaign.conversions)} sub="closed won" />
        <StatTile label="ROAS" value={formatRoas(r)} sub="revenue ÷ spend" />
        <StatTile
          label="Cost / lead"
          value={cpl === null ? "—" : formatMoney(cpl, campaign.currency)}
          sub="spend ÷ leads"
        />
      </section>

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-3 items-start">
        {/* Performance */}
        <Card className="flex flex-col gap-5 p-6 xl:col-span-2">
          <SectionHeader
            title="Weekly performance"
            subtitle="Revenue and spend across the campaign flight"
          />
          {isDraft ? (
            <div className="grid place-items-center rounded-tile bg-ground/60 py-16 text-center">
              <div className="flex max-w-xs flex-col gap-1">
                <p className="text-base font-bold text-ink">Not launched yet</p>
                <p className="text-sm text-muted">
                  This campaign is still a draft. Performance will appear here once it goes live.
                </p>
              </div>
            </div>
          ) : (
            <CampaignPerformanceChart weeks={weekly} currency={campaign.currency} />
          )}
        </Card>

        {/* Right column: budget + audience */}
        <div className="flex flex-col gap-6">
          <Card className="flex flex-col gap-4 p-6">
            <SectionHeader title="Budget & goal" />
            <div className="flex flex-col gap-1.5">
              <div className="flex items-baseline justify-between">
                <span className="display text-2xl font-extrabold tnum">
                  {formatMoney(campaign.spent_minor, campaign.currency)}
                </span>
                <span className="text-sm text-muted tnum">
                  of {formatMoney(campaign.budget_minor, campaign.currency)}
                </span>
              </div>
              <div className="h-2 w-full overflow-hidden rounded-full bg-ground">
                <div
                  className="h-full rounded-full bg-accent"
                  style={{
                    width: `${Math.min(
                      Math.max((campaign.spent_minor / campaign.budget_minor) * 100, 2),
                      100,
                    )}%`,
                  }}
                />
              </div>
              <span className="text-xs text-faint">
                {Math.round((campaign.spent_minor / campaign.budget_minor) * 100)}% of budget spent
              </span>
            </div>
            <div className="flex items-start gap-2.5 rounded-tile bg-ground/60 px-3 py-3">
              <TargetIcon className="mt-0.5 size-5 shrink-0 text-accent" />
              <div className="flex flex-col leading-tight">
                <span className="text-xs font-semibold uppercase tracking-wide text-faint">
                  Primary goal
                </span>
                <span className="text-sm font-semibold text-ink">{campaign.goal}</span>
              </div>
            </div>
            {acquisition !== null ? (
              <p className="text-sm text-muted">
                Customer acquisition cost{" "}
                <span className="font-semibold text-ink tnum">
                  {formatMoney(acquisition, campaign.currency)}
                </span>{" "}
                across {formatNumber(campaign.conversions)} deals.
              </p>
            ) : null}
          </Card>

          <Card className="flex flex-col gap-4 p-6">
            <SectionHeader title="Target audience" />
            {audience ? (
              <>
                <div className="flex items-center gap-3">
                  <span className="grid size-11 shrink-0 place-items-center rounded-tile bg-accent-soft text-accent-ink">
                    <UsersIcon className="size-5" />
                  </span>
                  <div className="flex flex-col leading-tight">
                    <span className="font-bold text-ink">{audience.name}</span>
                    <span className="text-sm text-muted tnum">
                      {formatNumber(audience.size)} reachable
                    </span>
                  </div>
                </div>
                <p className="rounded-tile bg-ground/60 px-3 py-2.5 text-sm text-ink-soft">
                  {audience.criteria}
                </p>
                <Link
                  to="/audiences"
                  className="inline-flex h-10 items-center justify-center gap-1.5 rounded-full border border-line bg-surface text-sm font-semibold text-ink hover:bg-ground transition-colors"
                >
                  Used by {usedBy} {usedBy === 1 ? "campaign" : "campaigns"}
                  <ArrowRightIcon className="size-4" />
                </Link>
              </>
            ) : (
              <p className="text-sm text-muted">No audience is attached to this campaign.</p>
            )}
          </Card>
        </div>
      </div>
    </div>
  );
}

function BackLink() {
  return (
    <Link
      to="/campaigns"
      className="inline-flex w-fit items-center gap-1.5 text-sm font-semibold text-muted hover:text-ink transition-colors"
    >
      <ArrowRightIcon className="size-4 rotate-180" />
      All campaigns
    </Link>
  );
}
