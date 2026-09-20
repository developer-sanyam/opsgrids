import { useCallback, useMemo, useState } from "react";
import type { CSSProperties } from "react";
import { Link, useNavigate } from "react-router-dom";

import { getPeople, getProjects, getTasks, getTimesheets, WEEK_DATES, TODAY } from "@/api/client";
import type { Person, Project, Task, TimesheetRow } from "@/api/types";
import { useAuth } from "@opsgrids/client";
import {
  activeProjects,
  needsAttention,
  projectHealth,
  tasksDueThisWeek,
  teamUtilization,
  weeklyBillableHours,
  countByStatus,
} from "@/data/derive";
import { BILLABLE_THIS_MONTH, KPI_TREND, UTILIZATION_TREND } from "@/data/samples";
import { formatHours, formatMoneyCompact } from "@/lib/format";
import { useAsync } from "@opsgrids/client";
import {
  Button,
  Card,
  EmptyState,
  ErrorState,
  KpiCard,
  NeedsAttention,
  ProjectHealth,
  SectionHeader,
  Skeleton,
  UtilizationTrend,
} from "@/ui";
import type { KpiTone } from "@/ui";
import {
  CalendarIcon,
  CheckCircleIcon,
  ClipboardCheckIcon,
  ClockIcon,
  GaugeIcon,
  LayersIcon,
  PlusIcon,
  SearchIcon,
  UsersIcon,
} from "@opsgrids/ui";

interface OverviewData {
  projects: Project[];
  tasks: Task[];
  timesheets: TimesheetRow[];
  people: Person[];
}

async function loadOverview(): Promise<OverviewData> {
  const [projects, tasks, timesheets, people] = await Promise.all([
    getProjects(),
    getTasks(),
    getTimesheets(),
    getPeople(),
  ]);
  return { projects, tasks, timesheets, people };
}

/** The Overview's top bar: breadcrumb + title, then search / period / new-project controls. */
function OverviewHeader({ orgName }: { orgName: string }) {
  const navigate = useNavigate();
  const [query, setQuery] = useState("");

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
            navigate(q ? `/projects?q=${encodeURIComponent(q)}` : "/projects");
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

        <span className="hidden h-11 items-center gap-2 rounded-full bg-surface px-4 text-sm font-semibold text-ink-soft shadow-card sm:inline-flex">
          <CalendarIcon className="size-4 text-muted" />
          <span className="tnum">This week · 24–30 Aug</span>
        </span>

        <Button size="md" leadingIcon={<PlusIcon className="size-4" />} onClick={() => navigate("/projects")}>
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
}

function buildKpis(data: OverviewData): KpiConfig[] {
  const active = activeProjects(data.projects);
  const due = tasksDueThisWeek(data.tasks, WEEK_DATES);
  const util = teamUtilization(data.timesheets, data.people);
  return [
    {
      key: "active",
      label: "Active projects",
      value: String(active),
      deltaPct: KPI_TREND.activeProjects.deltaPct,
      series: KPI_TREND.activeProjects.series,
      icon: <LayersIcon className="size-5" />,
      tone: "accent",
    },
    {
      key: "due",
      label: "Tasks due this week",
      value: String(due),
      deltaPct: KPI_TREND.tasksDue.deltaPct,
      series: KPI_TREND.tasksDue.series,
      icon: <ClipboardCheckIcon className="size-5" />,
      tone: "info",
      goodWhen: "down",
    },
    {
      key: "util",
      label: "Team utilization",
      value: `${util}%`,
      deltaPct: KPI_TREND.utilization.deltaPct,
      series: KPI_TREND.utilization.series,
      icon: <GaugeIcon className="size-5" />,
      tone: "positive",
    },
    {
      key: "billable",
      label: "Billable hours this month",
      value: BILLABLE_THIS_MONTH.hours.toLocaleString("en-US"),
      deltaPct: BILLABLE_THIS_MONTH.deltaPct,
      series: BILLABLE_THIS_MONTH.series,
      icon: <ClockIcon className="size-5" />,
      tone: "cyan",
    },
  ];
}

export function OverviewPage() {
  const { user } = useAuth();
  const state = useAsync(useCallback(loadOverview, []), []);

  const orgName =
    user?.organizations.find((o) => o.organization_id === user.organization_id)?.name ??
    user?.organizations[0]?.name ??
    "OpsGrids Studio";

  const kpis = useMemo(
    () => (state.status === "success" ? buildKpis(state.data) : []),
    [state],
  );

  const health = useMemo(
    () => (state.status === "success" ? projectHealth(state.data.projects) : []),
    [state],
  );
  const attention = useMemo(
    () => (state.status === "success" ? needsAttention(state.data.projects, TODAY) : []),
    [state],
  );

  const portfolio = useMemo(() => {
    if (state.status !== "success") return null;
    const active = state.data.projects.filter((p) => p.status !== "done");
    const budget = active.reduce((s, p) => s + p.budget_minor, 0);
    const spent = active.reduce((s, p) => s + p.spent_minor, 0);
    const currency = active[0]?.currency ?? "USD";
    const billableWeek = weeklyBillableHours(state.data.timesheets);
    return { budget, spent, currency, billableWeek, pct: budget === 0 ? 0 : Math.round((spent / budget) * 100) };
  }, [state]);

  const activeTotal = state.status === "success" ? activeProjects(state.data.projects) : 0;
  const deliveredCount = state.status === "success" ? countByStatus(state.data.projects, "done") : 0;
  const currentUtil = state.status === "success" ? teamUtilization(state.data.timesheets, state.data.people) : 0;

  return (
    <div className="rise flex flex-col gap-5">
      <OverviewHeader orgName={orgName} />

      {/* Row 1 — KPI cards */}
      <section aria-label="Key metrics" className="cascade grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {state.status === "loading"
          ? Array.from({ length: 4 }).map((_, i) => (
              <Card key={i} className="flex flex-col gap-4 p-5" style={{ "--i": i } as CSSProperties}>
                <div className="flex justify-between">
                  <Skeleton className="size-10 rounded-tile" />
                  <Skeleton className="h-8 w-20" />
                </div>
                <Skeleton className="h-4 w-28" />
                <Skeleton className="h-9 w-24 rounded-tile" />
              </Card>
            ))
          : null}
        {state.status === "error" ? (
          <Card className="p-5 sm:col-span-2 xl:col-span-4">
            <ErrorState message={state.error.message} />
          </Card>
        ) : null}
        {state.status === "success"
          ? kpis.map((kpi, i) => (
              <div key={kpi.key} style={{ "--i": i } as CSSProperties}>
                <KpiCard
                  label={kpi.label}
                  value={kpi.value}
                  deltaPct={kpi.deltaPct}
                  series={kpi.series}
                  icon={kpi.icon}
                  tone={kpi.tone}
                  goodWhen={kpi.goodWhen ?? "up"}
                />
              </div>
            ))
          : null}
      </section>

      {/* Row 2 — utilization trend · project health */}
      <section aria-label="Delivery health" className="grid grid-cols-1 gap-5 xl:grid-cols-3">
        <Card className="flex flex-col gap-4 p-6 xl:col-span-2">
          <SectionHeader title="Team utilization" subtitle="Billable share of capacity, by week" />
          {state.status === "success" ? (
            <UtilizationTrend current={currentUtil} deltaPct={KPI_TREND.utilization.deltaPct} series={UTILIZATION_TREND} />
          ) : state.status === "error" ? (
            <ErrorState message={state.error.message} />
          ) : (
            <Skeleton className="h-56 w-full rounded-tile" />
          )}
        </Card>

        <Card className="flex flex-col gap-5 p-6">
          <SectionHeader title="Project health" subtitle="Across the active portfolio" />
          {state.status === "success" && health.length > 0 ? (
            <ProjectHealth buckets={health} activeTotal={activeTotal} deliveredCount={deliveredCount} />
          ) : state.status === "error" ? (
            <ErrorState message={state.error.message} />
          ) : (
            <Skeleton className="h-56 w-full rounded-tile" />
          )}
        </Card>
      </section>

      {/* Row 3 — needs attention · portfolio value */}
      <section aria-label="Delivery detail" className="grid grid-cols-1 gap-5 xl:grid-cols-3 items-start">
        <Card className="flex flex-col gap-3 p-6 xl:col-span-2">
          <SectionHeader
            title="Needs attention"
            subtitle="Blocked, over budget, overdue or at risk"
            action={
              <Link
                to="/projects"
                className="text-sm font-semibold text-accent hover:text-accent-strong transition-colors"
              >
                All projects
              </Link>
            }
          />
          {state.status === "loading" ? (
            <div className="flex flex-col gap-3 py-2">
              {Array.from({ length: 4 }).map((_, i) => (
                <Skeleton key={i} className="h-14 w-full rounded-tile" />
              ))}
            </div>
          ) : null}
          {state.status === "error" ? <ErrorState message={state.error.message} /> : null}
          {state.status === "success" ? (
            attention.length > 0 ? (
              <NeedsAttention items={attention} />
            ) : (
              <EmptyState
                icon={<CheckCircleIcon className="size-6" />}
                title="Everything's on track"
                description="No project is blocked, over budget or overdue. Nice and calm."
              />
            )
          ) : null}
        </Card>

        <Card className="flex flex-col gap-5 p-6">
          <SectionHeader title="Portfolio value" subtitle="Active engagements" />
          {state.status === "success" && portfolio ? (
            <div className="flex flex-col gap-5">
              <div className="flex items-end justify-between">
                <div className="flex flex-col">
                  <span className="text-xs font-medium text-muted">Spent to date</span>
                  <span className="display text-2xl font-extrabold tnum">
                    {formatMoneyCompact(portfolio.spent, portfolio.currency)}
                  </span>
                </div>
                <div className="flex flex-col text-right">
                  <span className="text-xs font-medium text-muted">Budget</span>
                  <span className="text-lg font-bold tnum text-ink-soft">
                    {formatMoneyCompact(portfolio.budget, portfolio.currency)}
                  </span>
                </div>
              </div>

              <div className="flex flex-col gap-1.5">
                <div className="h-2.5 w-full overflow-hidden rounded-full bg-ground">
                  <div
                    className="h-full rounded-full bg-accent"
                    style={{ width: `${Math.min(portfolio.pct, 100)}%` }}
                  />
                </div>
                <p className="text-xs text-muted">
                  <span className="tnum font-semibold text-ink">{portfolio.pct}%</span> of budget
                  committed across <span className="tnum font-semibold text-ink">{activeTotal}</span> active
                  projects
                </p>
              </div>

              <dl className="grid grid-cols-2 gap-3 border-t border-line pt-4">
                <div className="flex flex-col gap-0.5 rounded-tile bg-ground px-3 py-2.5">
                  <dt className="inline-flex items-center gap-1.5 text-xs text-muted">
                    <ClockIcon className="size-3.5" /> Billable this week
                  </dt>
                  <dd className="tnum text-lg font-bold text-ink">{formatHours(portfolio.billableWeek)}</dd>
                </div>
                <div className="flex flex-col gap-0.5 rounded-tile bg-ground px-3 py-2.5">
                  <dt className="inline-flex items-center gap-1.5 text-xs text-muted">
                    <UsersIcon className="size-3.5" /> People on team
                  </dt>
                  <dd className="tnum text-lg font-bold text-ink">{state.data.people.length}</dd>
                </div>
              </dl>
            </div>
          ) : state.status === "error" ? (
            <ErrorState message={state.error.message} />
          ) : (
            <Skeleton className="h-56 w-full rounded-tile" />
          )}
        </Card>
      </section>
    </div>
  );
}
