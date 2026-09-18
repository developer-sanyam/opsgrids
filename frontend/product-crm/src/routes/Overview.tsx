import { useCallback, useMemo, useState } from "react";
import type { CSSProperties } from "react";
import { Link, useNavigate } from "react-router-dom";

import { getAllDeals, listActivities } from "@/api/client";
import type { Deal } from "@/api/types";
import { useAuth } from "@opsgrids/client";
import {
  needsAttention,
  openCount,
  pipelineValueMinor,
  stageFunnel,
  winRatePct,
} from "@/data/derive";
import { ACTIVITIES_THIS_WEEK, KPI_TRENDS } from "@/data/samples";
import { NOW_ISO, formatMoneyCompact } from "@/lib/format";
import { useAsync } from "@opsgrids/client";
import {
  ActivityTimeline,
  Button,
  Card,
  ErrorState,
  KpiCard,
  NeedsAttention,
  PipelineFunnel,
  PipelineTrend,
  SectionHeader,
  Skeleton,
} from "@/ui";
import type { KpiTone } from "@/ui";
import {
  ActivityIcon,
  BanknoteIcon,
  CalendarIcon,
  HandshakeIcon,
  PlusIcon,
  SearchIcon,
  TargetIcon,
} from "@opsgrids/ui";
import { SAMPLE_DATA } from "@/ui/sampleCopy";

const TODAY = NOW_ISO.slice(0, 10);
const CURRENCY = "INR";

const EMPTY_DEALS: Deal[] = [];

type Period = "this_quarter" | "this_month" | "this_week";

const PERIOD_RANGE: Record<Period, string> = {
  this_week: "18 Aug – 24 Aug 2026",
  this_month: "01 Aug – 24 Aug 2026",
  this_quarter: "Jul – Sep 2026",
};

/** The Overview top bar: breadcrumb + title, then search / period / New deal. */
function OverviewHeader({ orgName }: { orgName: string }) {
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [period, setPeriod] = useState<Period>("this_month");

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
        <form
          onSubmit={(e) => {
            e.preventDefault();
            const q = query.trim();
            navigate(q ? `/deals?q=${encodeURIComponent(q)}` : "/deals");
          }}
          className="hidden h-11 min-w-0 items-center gap-2 rounded-full bg-surface px-4 shadow-card focus-within:outline focus-within:outline-2 focus-within:outline-accent focus-within:outline-offset-2 md:flex md:w-60"
        >
          <SearchIcon className="size-[18px] shrink-0 text-faint" />
          <label htmlFor="overview-search" className="sr-only">
            Search deals
          </label>
          <input
            id="overview-search"
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search deals…"
            className="min-w-0 flex-1 bg-transparent text-sm text-ink placeholder:text-faint focus:outline-none"
          />
        </form>

        <span className="hidden h-11 items-center gap-2 rounded-full bg-surface px-4 text-sm font-semibold text-ink-soft shadow-card sm:inline-flex">
          <CalendarIcon className="size-4 text-muted" />
          <span className="tnum">{PERIOD_RANGE[period]}</span>
        </span>

        <label className="inline-flex h-11 items-center rounded-full bg-surface px-2 shadow-card">
          <span className="sr-only">Reporting period</span>
          <select
            value={period}
            onChange={(e) => setPeriod(e.target.value as Period)}
            className="h-full cursor-pointer rounded-full bg-transparent px-2 text-sm font-semibold text-ink focus:outline-none"
          >
            <option value="this_week">This week</option>
            <option value="this_month">This month</option>
            <option value="this_quarter">This quarter</option>
          </select>
        </label>

        <Button size="md" leadingIcon={<PlusIcon className="size-4" />} onClick={() => navigate("/deals")}>
          New deal
        </Button>
      </div>
    </header>
  );
}

interface KpiConfig {
  key: string;
  label: string;
  value: string;
  deltaPct: number;
  series: number[];
  icon: React.ReactNode;
  tone: KpiTone;
  sample: boolean;
}

function buildKpis(deals: Deal[]): KpiConfig[] {
  return [
    {
      key: "pipeline",
      label: "Pipeline value",
      value: formatMoneyCompact(pipelineValueMinor(deals), CURRENCY),
      deltaPct: KPI_TRENDS.pipeline.deltaPct,
      series: [...KPI_TRENDS.pipeline.series],
      icon: <BanknoteIcon className="size-5" />,
      tone: "accent",
      sample: false,
    },
    {
      key: "open",
      label: "Open deals",
      value: String(openCount(deals)),
      deltaPct: KPI_TRENDS.openDeals.deltaPct,
      series: [...KPI_TRENDS.openDeals.series],
      icon: <HandshakeIcon className="size-5" />,
      tone: "info",
      sample: false,
    },
    {
      key: "win",
      label: "Win rate",
      value: `${winRatePct(deals)}%`,
      deltaPct: KPI_TRENDS.winRate.deltaPct,
      series: [...KPI_TRENDS.winRate.series],
      icon: <TargetIcon className="size-5" />,
      tone: "positive",
      sample: false,
    },
    {
      key: "activities",
      label: "Activities this week",
      value: String(ACTIVITIES_THIS_WEEK.count),
      deltaPct: ACTIVITIES_THIS_WEEK.deltaPct,
      series: [...KPI_TRENDS.activities.series],
      icon: <ActivityIcon className="size-5" />,
      tone: "cyan",
      sample: true,
    },
  ];
}

export function OverviewPage() {
  const { user } = useAuth();
  const deals = useAsync(getAllDeals, []);
  const activities = useAsync(useCallback(() => listActivities({}), []), []);

  const orgName =
    user?.organizations.find((o) => o.organization_id === user.organization_id)?.name ??
    user?.organizations[0]?.name ??
    "OpsGrids";

  const items = useMemo(
    () => (deals.status === "success" ? deals.data.items : EMPTY_DEALS),
    [deals],
  );

  const kpis = useMemo(() => buildKpis(items), [items]);
  const funnel = useMemo(() => stageFunnel(items), [items]);
  const attention = useMemo(() => needsAttention(items, TODAY), [items]);
  const pipelineValue = useMemo(() => pipelineValueMinor(items), [items]);
  const recentActivities = useMemo(
    () => (activities.status === "success" ? activities.data.slice(0, 6) : []),
    [activities],
  );

  return (
    <div className="rise flex flex-col gap-5">
      <OverviewHeader orgName={orgName} />

      {/* Row 1 — KPI cards */}
      <section aria-label="Key metrics" className="cascade grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {deals.status === "loading"
          ? Array.from({ length: 4 }).map((_, i) => (
              <Card key={i} className="flex flex-col gap-4 p-5" style={{ "--i": i } as CSSProperties}>
                <div className="flex justify-between">
                  <Skeleton className="size-10 rounded-tile" />
                  <Skeleton className="h-8 w-20" />
                </div>
                <Skeleton className="h-4 w-24" />
                <Skeleton className="h-9 w-20 rounded-tile" />
              </Card>
            ))
          : null}
        {deals.status === "error" ? (
          <Card className="p-5 sm:col-span-2 xl:col-span-4">
            <ErrorState message={deals.error.message} />
          </Card>
        ) : null}
        {deals.status === "success"
          ? kpis.map((kpi, i) => (
              <div key={kpi.key} style={{ "--i": i } as CSSProperties}>
                <KpiCard
                  label={kpi.label}
                  value={kpi.value}
                  deltaPct={kpi.deltaPct}
                  series={kpi.series}
                  icon={kpi.icon}
                  tone={kpi.tone}
                  sample={kpi.sample ? SAMPLE_DATA : false}
                />
              </div>
            ))
          : null}
      </section>

      {/* Row 2 — pipeline trend · by-stage funnel */}
      <section aria-label="Pipeline overview" className="grid grid-cols-1 gap-5 xl:grid-cols-3">
        <Card className="flex flex-col gap-4 p-6 xl:col-span-2">
          <SectionHeader title="Pipeline value over time" subtitle="Open value, month by month" />
          {deals.status === "success" ? (
            <PipelineTrend currentMinor={pipelineValue} currency={CURRENCY} />
          ) : (
            <Skeleton className="h-56 w-full rounded-tile" />
          )}
        </Card>

        <Card className="flex flex-col gap-5 p-6 xl:col-span-1">
          <SectionHeader title="Deals by stage" subtitle="Open pipeline funnel" />
          {deals.status === "success" ? (
            <PipelineFunnel rollups={funnel} currency={CURRENCY} />
          ) : deals.status === "error" ? (
            <ErrorState message={deals.error.message} />
          ) : (
            <Skeleton className="h-56 w-full rounded-tile" />
          )}
        </Card>
      </section>

      {/* Row 3 — needs attention · recent activity */}
      <section aria-label="Follow-ups" className="grid grid-cols-1 gap-5 xl:grid-cols-3 items-start">
        <Card className="flex flex-col gap-2 p-6 xl:col-span-2">
          <SectionHeader
            title="Needs attention"
            subtitle="Overdue, closing soon, or going quiet"
            action={
              <Link
                to="/deals"
                className="text-sm font-semibold text-accent hover:text-accent-strong transition-colors"
              >
                All deals
              </Link>
            }
          />
          {deals.status === "loading" ? (
            <div className="flex flex-col gap-3 py-2">
              {Array.from({ length: 5 }).map((_, i) => (
                <Skeleton key={i} className="h-14 w-full rounded-tile" />
              ))}
            </div>
          ) : null}
          {deals.status === "error" ? <ErrorState message={deals.error.message} /> : null}
          {deals.status === "success" ? <NeedsAttention items={attention} /> : null}
        </Card>

        <Card className="flex flex-col gap-5 p-6">
          <SectionHeader
            title="Recent activity"
            action={
              <Link
                to="/activities"
                className="text-sm font-semibold text-accent hover:text-accent-strong transition-colors"
              >
                View all
              </Link>
            }
          />
          {activities.status === "loading" ? (
            <div className="flex flex-col gap-4 py-2">
              {Array.from({ length: 5 }).map((_, i) => (
                <Skeleton key={i} className="h-12 w-full rounded-tile" />
              ))}
            </div>
          ) : null}
          {activities.status === "error" ? <ErrorState message={activities.error.message} /> : null}
          {activities.status === "success" ? (
            <ActivityTimeline activities={recentActivities} />
          ) : null}
        </Card>
      </section>
    </div>
  );
}
