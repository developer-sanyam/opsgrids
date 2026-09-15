import { useMemo, useState } from "react";
import type { CSSProperties } from "react";
import { useNavigate } from "react-router-dom";

import { getCampaigns, getRevenueSpendSeries, TRENDS_ARE_SAMPLE } from "@/api/client";
import type { Campaign } from "@/api/types";
import { useAuth } from "@opsgrids/client";
import { channelPerformance, needsAttention, overviewTotals } from "@/data/derive";
import { useAsync } from "@opsgrids/client";
import {
  AlertTriangleIcon,
  FunnelIcon,
  GaugeIcon,
  PlusIcon,
  TargetIcon,
  TrendingUpIcon,
  cn,
} from "@opsgrids/ui";
import { formatMoney, formatMoneyCompact, formatRoas } from "@/lib/format";
import {
  Button,
  Card,
  ChannelBreakdown,
  ErrorState,
  KpiCard,
  NeedsAttention,
  RevenueSpendChart,
  SectionHeader,
  Skeleton,
} from "@/ui";
import type { KpiTone } from "@/ui";
import { SAMPLE_TREND } from "@/ui/sampleCopy";

const CURRENCY = "INR";

/** A stable empty reference so the derived-data memos don't churn while loading. */
const NO_CAMPAIGNS: Campaign[] = [];

type Period = "month" | "quarter" | "year";

const PERIODS: { value: Period; label: string; caption: string }[] = [
  { value: "month", label: "Month", caption: "vs last month" },
  { value: "quarter", label: "Quarter", caption: "vs last quarter" },
  { value: "year", label: "Year", caption: "vs last year" },
];

interface KpiConfig {
  key: string;
  label: string;
  value: string;
  deltaPct: number;
  goodWhen: "up" | "down";
  series: number[];
  icon: React.ReactNode;
  tone: KpiTone;
}

function buildKpis(campaigns: Campaign[]): KpiConfig[] {
  const t = overviewTotals(campaigns);
  return [
    {
      key: "revenue",
      label: "Revenue attributed",
      value: formatMoneyCompact(t.revenueMinor, CURRENCY),
      deltaPct: 18.4,
      goodWhen: "up",
      series: [612, 640, 668, 690, 712, 760, 800, 828],
      icon: <TrendingUpIcon className="size-5" />,
      tone: "accent",
    },
    {
      key: "pipeline",
      label: "Pipeline generated",
      value: formatMoneyCompact(t.pipelineMinor, CURRENCY),
      deltaPct: 11.2,
      goodWhen: "up",
      series: [1500, 1560, 1600, 1650, 1700, 1760, 1790, 1802],
      icon: <FunnelIcon className="size-5" />,
      tone: "info",
    },
    {
      key: "cpl",
      label: "Cost per lead",
      value: t.cplMinor === null ? "—" : formatMoney(t.cplMinor, CURRENCY),
      deltaPct: -7.9,
      goodWhen: "down",
      series: [4200, 4100, 3950, 3880, 3800, 3720, 3680, 3649],
      icon: <TargetIcon className="size-5" />,
      tone: "cyan",
    },
    {
      key: "roas",
      label: "ROAS",
      value: formatRoas(t.roas),
      deltaPct: 6.5,
      goodWhen: "up",
      series: [390, 400, 410, 415, 420, 430, 438, 442],
      icon: <GaugeIcon className="size-5" />,
      tone: "positive",
    },
  ];
}

function OverviewHeader({
  orgName,
  period,
  onPeriod,
}: {
  orgName: string;
  period: Period;
  onPeriod: (p: Period) => void;
}) {
  const navigate = useNavigate();
  return (
    <header className="flex flex-col gap-4 xl:flex-row xl:items-end xl:justify-between">
      <div className="flex flex-col gap-1">
        <nav aria-label="Breadcrumb" className="text-sm font-medium text-muted">
          Workspace <span className="text-faint">/</span>{" "}
          <span className="text-ink-soft">{orgName}</span>
        </nav>
        <h1 className="display text-3xl font-extrabold leading-tight sm:text-[2.1rem]">Overview</h1>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <div
          role="tablist"
          aria-label="Reporting period"
          className="flex items-center rounded-full bg-surface p-0.5 shadow-card"
        >
          {PERIODS.map((p) => (
            <button
              key={p.value}
              type="button"
              role="tab"
              aria-selected={period === p.value}
              onClick={() => onPeriod(p.value)}
              className={cn(
                "rounded-full px-4 h-9 text-sm font-semibold transition-colors",
                period === p.value ? "bg-accent text-white" : "text-muted hover:text-ink",
              )}
            >
              {p.label}
            </button>
          ))}
        </div>
        <Button leadingIcon={<PlusIcon className="size-4" />} onClick={() => navigate("/campaigns")}>
          New campaign
        </Button>
      </div>
    </header>
  );
}

export function OverviewPage() {
  const { user } = useAuth();
  const [period, setPeriod] = useState<Period>("quarter");
  const campaigns = useAsync(getCampaigns, []);
  const series = useAsync(getRevenueSpendSeries, []);

  const orgName =
    user?.organizations.find((o) => o.organization_id === user.organization_id)?.name ??
    user?.organizations[0]?.name ??
    "OpsGrids";

  const items = useMemo(
    () => (campaigns.status === "success" ? campaigns.data.items : NO_CAMPAIGNS),
    [campaigns],
  );

  const kpis = useMemo(() => buildKpis(items), [items]);
  const channels = useMemo(() => channelPerformance(items), [items]);
  const attention = useMemo(() => needsAttention(items), [items]);
  const caption = PERIODS.find((p) => p.value === period)?.caption ?? "";

  return (
    <div className="rise flex flex-col gap-5">
      <OverviewHeader orgName={orgName} period={period} onPeriod={setPeriod} />

      {/* Row 1 — KPI cards */}
      <section
        aria-label="Key metrics"
        className="cascade grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4"
      >
        {campaigns.status === "loading"
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
        {campaigns.status === "error" ? (
          <Card className="p-5 sm:col-span-2 xl:col-span-4">
            <ErrorState message={campaigns.error.message} />
          </Card>
        ) : null}
        {campaigns.status === "success"
          ? kpis.map((kpi, i) => (
              <div key={kpi.key} style={{ "--i": i } as CSSProperties}>
                <KpiCard
                  label={kpi.label}
                  value={kpi.value}
                  deltaPct={kpi.deltaPct}
                  goodWhen={kpi.goodWhen}
                  series={kpi.series}
                  icon={kpi.icon}
                  tone={kpi.tone}
                  caption={caption}
                  sample={TRENDS_ARE_SAMPLE ? SAMPLE_TREND : false}
                />
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
          {series.status === "success" ? (
            <RevenueSpendChart series={series.data} currency={CURRENCY} />
          ) : series.status === "error" ? (
            <ErrorState message={series.error.message} />
          ) : (
            <Skeleton className="h-60 w-full rounded-tile" />
          )}
        </Card>

        <Card className="flex flex-col gap-5 p-6 xl:col-span-1">
          <SectionHeader title="By channel" subtitle="Where revenue comes from" />
          {campaigns.status === "success" && channels.length > 0 ? (
            <ChannelBreakdown data={channels} currency={CURRENCY} />
          ) : campaigns.status === "error" ? (
            <ErrorState message={campaigns.error.message} />
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
              campaigns.status === "success" && attention.length > 0 ? (
                <span className="inline-flex items-center gap-1.5 rounded-full bg-negative-soft px-3 py-1 text-xs font-bold text-negative">
                  <AlertTriangleIcon className="size-3.5" />
                  {attention.length} to review
                </span>
              ) : undefined
            }
          />
          {campaigns.status === "loading" ? (
            <div className="flex flex-col gap-2">
              {Array.from({ length: 2 }).map((_, i) => (
                <Skeleton key={i} className="h-16 w-full rounded-tile" />
              ))}
            </div>
          ) : campaigns.status === "error" ? (
            <ErrorState message={campaigns.error.message} />
          ) : (
            <NeedsAttention items={attention} />
          )}
        </Card>
      </section>
    </div>
  );
}
