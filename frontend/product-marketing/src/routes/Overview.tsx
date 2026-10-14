import { useCallback, useMemo } from "react";
import type { CSSProperties } from "react";
import { useNavigate } from "react-router-dom";

import { getCampaigns, getOverview } from "@/api/client";
import type { Campaign } from "@/api/types";
import { useAuth } from "@opsgrids/client";
import { needsAttention } from "@/data/derive";
import { problemMessage, useAsync } from "@opsgrids/client";
import {
  AlertTriangleIcon,
  FunnelIcon,
  GaugeIcon,
  PlusIcon,
  TargetIcon,
  TrendingUpIcon,
} from "@opsgrids/ui";
import { formatMoney, formatMoneyCompact, formatRoas } from "@/lib/format";
import {
  Button,
  Card,
  ChannelBreakdown,
  EmptyState,
  ErrorState,
  KpiCard,
  NeedsAttention,
  RevenueSpendChart,
  SectionHeader,
  Skeleton,
} from "@/ui";
import type { KpiTone } from "@/ui";

/** A stable empty reference so the derived-data memo doesn't churn while loading. */
const NO_CAMPAIGNS: Campaign[] = [];

/**
 * Live/paused campaigns worth reviewing for "Needs attention" are pulled in two bounded, status-
 * filtered pages rather than one 200-row cap over every status — a status filter the server
 * already supports. This still bounds each status to a generous but finite working set; an org
 * running more concurrent live-or-paused campaigns than that sees only the largest slice. The KPI
 * headlines above never share this limit — they come from the server's own rollup.
 */
const ATTENTION_PAGE_SIZE = 100;

interface KpiConfig {
  key: string;
  label: string;
  value: string;
  icon: React.ReactNode;
  tone: KpiTone;
}

function OverviewHeader({ orgName }: { orgName: string }) {
  const navigate = useNavigate();
  return (
    <header className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
      <div className="flex flex-col gap-1">
        <nav aria-label="Breadcrumb" className="text-sm font-medium text-muted">
          Workspace <span className="text-faint">/</span>{" "}
          <span className="text-ink-soft">{orgName}</span>
        </nav>
        <h1 className="display text-3xl font-extrabold leading-tight sm:text-[2.1rem]">Overview</h1>
      </div>
      <Button
        leadingIcon={<PlusIcon className="size-4" />}
        onClick={() => navigate("/campaigns?new=1")}
      >
        New campaign
      </Button>
    </header>
  );
}

export function OverviewPage() {
  const { user } = useAuth();
  const overview = useAsync(getOverview, []);
  const attention = useAsync(
    useCallback(
      () =>
        Promise.all([
          getCampaigns({ status: "live", size: ATTENTION_PAGE_SIZE }),
          getCampaigns({ status: "paused", size: ATTENTION_PAGE_SIZE }),
        ]).then(([live, paused]) => [...live.items, ...paused.items]),
      [],
    ),
    [],
  );

  const orgName =
    user?.organizations.find((o) => o.organization_id === user.organization_id)?.name ??
    user?.organizations[0]?.name ??
    "OpsGrids";

  const attentionItems = useMemo(
    () => needsAttention(attention.status === "success" ? attention.data : NO_CAMPAIGNS),
    [attention],
  );

  const kpis: KpiConfig[] | null =
    overview.status === "success"
      ? [
          {
            key: "revenue",
            label: "Revenue attributed",
            value: formatMoneyCompact(overview.data.revenue_minor, overview.data.currency),
            icon: <TrendingUpIcon className="size-5" />,
            tone: "accent",
          },
          {
            key: "pipeline",
            label: "Pipeline generated",
            value: formatMoneyCompact(overview.data.pipeline_minor, overview.data.currency),
            icon: <FunnelIcon className="size-5" />,
            tone: "info",
          },
          {
            key: "cpl",
            label: "Cost per lead",
            value:
              overview.data.cpl_minor === null
                ? "No leads yet"
                : formatMoney(overview.data.cpl_minor, overview.data.currency),
            icon: <TargetIcon className="size-5" />,
            tone: "cyan",
          },
          {
            key: "roas",
            label: "ROAS",
            value: overview.data.spend_minor === 0 ? "No spend yet" : formatRoas(overview.data.roas),
            icon: <GaugeIcon className="size-5" />,
            tone: "positive",
          },
        ]
      : null;

  return (
    <div className="rise flex flex-col gap-5">
      <OverviewHeader orgName={orgName} />

      {/* Row 1 — KPI cards */}
      <section
        aria-label="Key metrics"
        className="cascade grid grid-cols-2 gap-4 xl:grid-cols-4"
      >
        {overview.status === "loading"
          ? Array.from({ length: 4 }).map((_, i) => (
              <Card key={i} className="flex flex-col gap-4 p-5" style={{ "--i": i } as CSSProperties}>
                <div className="flex justify-between">
                  <Skeleton className="size-10 rounded-tile" />
                  <Skeleton className="h-8 w-20" />
                </div>
                <Skeleton className="h-4 w-24" />
                <Skeleton className="h-9 w-24 rounded-tile" />
              </Card>
            ))
          : null}
        {overview.status === "error" ? (
          <Card className="p-5 col-span-2 xl:col-span-4">
            <ErrorState
              title="Couldn't load the overview"
              message={problemMessage(overview.error)}
              onRetry={overview.reload}
            />
          </Card>
        ) : null}
        {kpis
          ? kpis.map((kpi, i) => (
              <div key={kpi.key} style={{ "--i": i } as CSSProperties}>
                <KpiCard label={kpi.label} value={kpi.value} icon={kpi.icon} tone={kpi.tone} />
              </div>
            ))
          : null}
      </section>

      {/* Row 2 — revenue vs spend · channel breakdown */}
      <section aria-label="Performance" className="grid grid-cols-1 gap-5 xl:grid-cols-3">
        <Card className="flex flex-col gap-5 p-6 xl:col-span-2">
          <SectionHeader
            title="Revenue vs spend"
            subtitle="Attributed revenue against marketing spend, monthly"
          />
          {overview.status === "success" ? (
            overview.data.revenue_vs_spend.length > 0 ? (
              <RevenueSpendChart series={overview.data.revenue_vs_spend} currency={overview.data.currency} />
            ) : (
              <EmptyState
                icon={<TrendingUpIcon className="size-6" />}
                title="No attribution data yet"
                description="This chart fills in once revenue and spend are being recorded against a campaign — there is nothing fabricated here to show in the meantime."
              />
            )
          ) : overview.status === "error" ? (
            <ErrorState message={problemMessage(overview.error)} onRetry={overview.reload} />
          ) : (
            <Skeleton className="h-60 w-full rounded-tile" />
          )}
        </Card>

        <Card className="flex flex-col gap-5 p-6 xl:col-span-1">
          <SectionHeader title="By channel" subtitle="Where revenue comes from" />
          {overview.status === "success" ? (
            overview.data.channels.length > 0 ? (
              <ChannelBreakdown data={overview.data.channels} currency={overview.data.currency} />
            ) : (
              <EmptyState
                title="No campaigns yet"
                description="Create a campaign to see spend and revenue broken down by channel."
              />
            )
          ) : overview.status === "error" ? (
            <ErrorState message={problemMessage(overview.error)} onRetry={overview.reload} />
          ) : (
            <Skeleton className="h-60 w-full rounded-tile" />
          )}
        </Card>
      </section>

      {/* Row 3 — needs attention */}
      <section aria-label="Needs attention">
        <Card className="flex flex-col gap-5 p-6">
          <SectionHeader
            title="Needs attention"
            subtitle="Live campaigns spending below the 2× ROAS floor"
            action={
              attention.status === "success" && attentionItems.length > 0 ? (
                <span className="inline-flex items-center gap-1.5 rounded-full bg-negative-soft px-3 py-1 text-xs font-bold text-negative">
                  <AlertTriangleIcon className="size-3.5" />
                  {attentionItems.length} to review
                </span>
              ) : undefined
            }
          />
          {attention.status === "loading" ? (
            <div className="flex flex-col gap-2">
              {Array.from({ length: 2 }).map((_, i) => (
                <Skeleton key={i} className="h-16 w-full rounded-tile" />
              ))}
            </div>
          ) : attention.status === "error" ? (
            <ErrorState message={problemMessage(attention.error)} onRetry={attention.reload} />
          ) : (
            <NeedsAttention items={attentionItems} />
          )}
        </Card>
      </section>
    </div>
  );
}
