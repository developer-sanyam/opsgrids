import { useMemo, useState } from "react";
import type { CSSProperties } from "react";
import { Link, useNavigate } from "react-router-dom";

import { getAllProjects, listMilestones, TRENDS_ARE_SAMPLE } from "@/api/client";
import { NOW, RESOURCE_UTILIZATION } from "@/api/fixtures";
import type { MilestoneOut, ProjectOut } from "@/api/types";
import { useAuth } from "@opsgrids/client";
import {
  activeProjects,
  averageProgress,
  healthBreakdown,
  milestonesDue,
  needsAttention,
  onTimeFraction,
  totalBudgetMinor,
  totalSpentMinor,
} from "@/data/derive";
import { formatMoney, formatPct } from "@/lib/format";
import { useAsync } from "@opsgrids/client";
import {
  Burndown,
  Button,
  Card,
  ErrorState,
  HealthBreakdown,
  KpiCard,
  NeedsAttention,
  ProgressBar,
  SampleChip,
  SectionHeader,
  Skeleton,
} from "@/ui";
import type { KpiTone } from "@/ui";
import {
  ArrowRightIcon,
  BellIcon,
  BriefcaseIcon,
  CalendarIcon,
  FlagIcon,
  GaugeIcon,
  PlusIcon,
  SearchIcon,
  TargetIcon,
} from "@opsgrids/ui";
import { SAMPLE_DATA } from "@/ui/sampleCopy";

/** A stable empty reference so the derived-data memos don't churn while loading. */
const EMPTY_PROJECTS: ProjectOut[] = [];
const EMPTY_MILESTONES: MilestoneOut[] = [];

type Period = "week" | "month" | "quarter";

const PERIOD_RANGE: Record<Period, string> = {
  week: "18 Aug – 24 Aug 2026",
  month: "01 Aug – 31 Aug 2026",
  quarter: "Jul – Sep 2026",
};

/** The Overview's top bar: breadcrumb + title, then the search / date / period / add controls. */
function OverviewHeader({ orgName }: { orgName: string }) {
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [period, setPeriod] = useState<Period>("month");

  return (
    <header className="flex flex-col gap-4 xl:flex-row xl:items-end xl:justify-between">
      <div className="flex flex-col gap-1">
        <nav aria-label="Breadcrumb" className="text-sm font-medium text-muted">
          Workspace <span className="text-faint">/</span>{" "}
          <span className="text-ink-soft">{orgName}</span>
        </nav>
        <h1 className="display text-3xl font-extrabold leading-tight sm:text-[2.1rem]">
          Overview
        </h1>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            const q = query.trim();
            navigate(q ? `/portfolio?q=${encodeURIComponent(q)}` : "/portfolio");
          }}
          className="hidden h-11 min-w-0 items-center gap-2 rounded-full bg-surface px-4 shadow-card focus-within:outline focus-within:outline-2 focus-within:outline-accent focus-within:outline-offset-2 md:flex md:w-60"
        >
          <SearchIcon className="size-[18px] shrink-0 text-faint" />
          <label htmlFor="overview-search" className="sr-only">
            Search projects
          </label>
          <input
            id="overview-search"
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search projects…"
            className="min-w-0 flex-1 bg-transparent text-sm text-ink placeholder:text-faint focus:outline-none"
          />
        </form>

        <Link
          to="/milestones"
          aria-label="Notifications"
          className="relative grid size-11 shrink-0 place-items-center rounded-full bg-surface text-ink-soft shadow-card hover:bg-ground transition-colors"
        >
          <BellIcon className="size-5" />
          <span className="absolute right-2.5 top-2.5 size-2 rounded-full bg-accent ring-2 ring-surface" aria-hidden />
        </Link>

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
            <option value="week">This week</option>
            <option value="month">This month</option>
            <option value="quarter">This quarter</option>
          </select>
        </label>

        <Button
          size="md"
          leadingIcon={<PlusIcon className="size-4" />}
          onClick={() => navigate("/portfolio")}
        >
          New project
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
  goodWhen?: "down";
  sample?: boolean;
}

function buildKpis(projects: ProjectOut[], milestones: MilestoneOut[]): KpiConfig[] {
  const active = activeProjects(projects);
  const onTime = onTimeFraction(milestones);
  const due = milestonesDue(milestones, NOW);
  return [
    {
      key: "active",
      label: "Active projects",
      value: String(active),
      deltaPct: 8.3,
      series: [9, 10, 11, 11, 12, 12, active],
      icon: <BriefcaseIcon className="size-5" />,
      tone: "accent",
    },
    {
      key: "on_time",
      label: "On-time delivery",
      value: formatPct(onTime),
      deltaPct: 2.4,
      series: [72, 74, 73, 76, 79, 78, Math.round(onTime * 100)],
      icon: <TargetIcon className="size-5" />,
      tone: "positive",
    },
    {
      key: "due",
      label: "Milestones due (14d)",
      value: String(due),
      deltaPct: 12.5,
      series: [3, 4, 4, 5, 5, 6, due],
      icon: <FlagIcon className="size-5" />,
      tone: "info",
    },
    {
      key: "utilization",
      label: "Resource utilization",
      value: `${RESOURCE_UTILIZATION.pct}%`,
      deltaPct: RESOURCE_UTILIZATION.delta,
      series: RESOURCE_UTILIZATION.series,
      icon: <GaugeIcon className="size-5" />,
      tone: "cyan",
      sample: true,
    },
  ];
}

/** A compact portfolio snapshot: budget vs spend, average progress, and a link into the portfolio. */
function PortfolioSnapshot({ projects }: { projects: ProjectOut[] }) {
  const budget = totalBudgetMinor(projects);
  const spent = totalSpentMinor(projects);
  const spentPct = budget === 0 ? 0 : Math.round((spent / budget) * 100);
  const avg = averageProgress(projects.filter((p) => p.phase !== "live"));

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-col gap-1">
        <span className="text-sm text-muted">Committed budget</span>
        <span className="display text-3xl font-extrabold tnum leading-none">
          {formatMoney(budget)}
        </span>
      </div>

      <div className="flex flex-col gap-2">
        <div className="flex items-center justify-between text-sm">
          <span className="text-muted">Spent to date</span>
          <span className="font-semibold text-ink tnum">
            {formatMoney(spent)} · {spentPct}%
          </span>
        </div>
        <ProgressBar value={spentPct} />
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div className="rounded-tile bg-ground px-3 py-3">
          <span className="block text-xs text-muted">Avg. progress</span>
          <span className="text-lg font-extrabold tnum">{avg}%</span>
        </div>
        <div className="rounded-tile bg-ground px-3 py-3">
          <span className="block text-xs text-muted">In delivery</span>
          <span className="text-lg font-extrabold tnum">{activeProjects(projects)}</span>
        </div>
      </div>

      <Link
        to="/portfolio"
        className="inline-flex h-10 items-center justify-center gap-1.5 rounded-full border border-line bg-surface text-sm font-semibold text-ink hover:bg-ground transition-colors"
      >
        Open portfolio
        <ArrowRightIcon className="size-4" />
      </Link>
    </div>
  );
}

export function OverviewPage() {
  const { user } = useAuth();
  const projectsState = useAsync(getAllProjects, []);
  const milestonesState = useAsync(listMilestones, []);

  const orgName =
    user?.organizations.find((o) => o.organization_id === user.organization_id)?.name ??
    user?.organizations[0]?.name ??
    "OpsGrids";

  const projects = useMemo(
    () => (projectsState.status === "success" ? projectsState.data.items : EMPTY_PROJECTS),
    [projectsState],
  );
  const milestones = useMemo(
    () => (milestonesState.status === "success" ? milestonesState.data.items : EMPTY_MILESTONES),
    [milestonesState],
  );

  const kpis = useMemo(() => buildKpis(projects, milestones), [projects, milestones]);
  const health = useMemo(() => healthBreakdown(projects), [projects]);
  const attention = useMemo(() => needsAttention(milestones), [milestones]);

  const ready = projectsState.status === "success" && milestonesState.status === "success";
  const failed = projectsState.status === "error" || milestonesState.status === "error";
  const errorMessage =
    (projectsState.status === "error" && projectsState.error.message) ||
    (milestonesState.status === "error" && milestonesState.error.message) ||
    "Couldn't load the portfolio.";

  return (
    <div className="rise flex flex-col gap-5">
      <OverviewHeader orgName={orgName} />

      {/* Row 1 — KPI cards */}
      <section aria-label="Key metrics" className="cascade grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {!ready && !failed
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
        {failed ? (
          <Card className="p-5 sm:col-span-2 xl:col-span-4">
            <ErrorState message={errorMessage} />
          </Card>
        ) : null}
        {ready
          ? kpis.map((kpi, i) => (
              <div key={kpi.key} style={{ "--i": i } as CSSProperties}>
                <KpiCard
                  label={kpi.label}
                  value={kpi.value}
                  deltaPct={kpi.deltaPct}
                  series={kpi.series}
                  icon={kpi.icon}
                  tone={kpi.tone}
                  {...(kpi.goodWhen ? { goodWhen: kpi.goodWhen } : {})}
                  {...(kpi.sample ? { sample: SAMPLE_DATA } : {})}
                />
              </div>
            ))
          : null}
      </section>

      {/* Row 2 — burndown · health breakdown */}
      <section aria-label="Delivery overview" className="grid grid-cols-1 gap-5 xl:grid-cols-3">
        <Card className="flex flex-col gap-4 p-6 xl:col-span-2">
          <SectionHeader
            title="Release burndown"
            subtitle="Remaining scope against the plan"
            {...(TRENDS_ARE_SAMPLE ? { action: <SampleChip {...SAMPLE_DATA} /> } : {})}
          />
          {ready ? (
            <Burndown />
          ) : failed ? (
            <ErrorState message={errorMessage} />
          ) : (
            <Skeleton className="h-64 w-full rounded-tile" />
          )}
        </Card>

        <Card className="flex flex-col gap-5 p-6 xl:col-span-1">
          <SectionHeader title="Project health" subtitle="Across the portfolio" />
          {ready ? (
            <HealthBreakdown slices={health} total={projects.length} />
          ) : failed ? (
            <ErrorState message={errorMessage} />
          ) : (
            <Skeleton className="h-56 w-full rounded-tile" />
          )}
        </Card>
      </section>

      {/* Row 3 — needs attention · portfolio snapshot */}
      <section aria-label="Attention and portfolio" className="grid grid-cols-1 gap-5 xl:grid-cols-3 items-start">
        <Card className="flex flex-col gap-4 p-6 xl:col-span-2">
          <SectionHeader
            title="Needs attention"
            subtitle="Milestones slipping or at risk"
            action={
              <Link
                to="/milestones"
                className="text-sm font-semibold text-accent hover:text-accent-strong transition-colors"
              >
                All milestones
              </Link>
            }
          />
          {ready ? (
            <NeedsAttention milestones={attention} />
          ) : failed ? (
            <ErrorState message={errorMessage} />
          ) : (
            <div className="flex flex-col gap-3 py-2">
              {Array.from({ length: 4 }).map((_, i) => (
                <Skeleton key={i} className="h-12 w-full rounded-tile" />
              ))}
            </div>
          )}
        </Card>

        <Card className="flex flex-col gap-5 p-6 xl:col-span-1">
          <SectionHeader title="Portfolio" subtitle="Budget & delivery" />
          {ready ? (
            <PortfolioSnapshot projects={projects} />
          ) : (
            <Skeleton className="h-64 w-full rounded-tile" />
          )}
        </Card>
      </section>
    </div>
  );
}
