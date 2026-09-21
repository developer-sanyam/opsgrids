import { useCallback, useMemo, useState } from "react";
import type { CSSProperties } from "react";
import { Link, useNavigate } from "react-router-dom";

import { getAllMilestones, getAllProjects, TRENDS_ARE_SAMPLE } from "@/api/client";
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
import { formatMoney, formatPct, today } from "@/lib/format";
import { problemMessage, useAsync } from "@opsgrids/client";
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
  StatCard,
} from "@/ui";
import type { KpiTone } from "@/ui";
import {
  ArrowRightIcon,
  BriefcaseIcon,
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

/**
 * A representative resource-utilization figure and trend for the KPI row. `services/projects` has
 * no capacity source at all — not even the constant `/overview` exposes reaches this app, since
 * the SPA derives its rollups from the project and milestone lists rather than re-implementing
 * that endpoint (see the audit's "Must not be built"). Kept only because it's chip-marked below as
 * sample data, exactly like the burndown chart, and never presented as a live figure.
 */
const SAMPLE_UTILIZATION_PCT = 87;
const SAMPLE_UTILIZATION_SERIES = [79, 82, 80, 84, 86, 85, 87];

/** The Overview's top bar: breadcrumb + title, then the search / add controls. Search is visible
 *  at every width — a control hidden below `md` is a control most visits never see. */
function OverviewHeader({ orgName }: { orgName: string }) {
  const navigate = useNavigate();
  const [query, setQuery] = useState("");

  return (
    <header className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
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
          className="flex h-11 min-w-0 flex-1 items-center gap-2 rounded-full bg-surface px-4 shadow-card focus-within:outline focus-within:outline-2 focus-within:outline-accent focus-within:outline-offset-2 sm:w-60 sm:flex-none"
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

        <Button
          size="md"
          leadingIcon={<PlusIcon className="size-4" />}
          onClick={() => navigate("/portfolio?new=1")}
        >
          New project
        </Button>
      </div>
    </header>
  );
}

interface StatConfig {
  key: string;
  label: string;
  value: string;
  icon: React.ReactNode;
  tone: KpiTone;
}

/** The three headline figures with no history endpoint behind them — real numbers, no invented
 *  trend, rendered as plain `StatCard`s rather than a `KpiCard` with a fabricated delta. */
function buildStats(projects: ProjectOut[], milestones: MilestoneOut[]): StatConfig[] {
  const active = activeProjects(projects);
  const onTime = onTimeFraction(milestones);
  const due = milestonesDue(milestones, today());
  return [
    {
      key: "active",
      label: "Active projects",
      value: String(active),
      icon: <BriefcaseIcon className="size-5" />,
      tone: "accent",
    },
    {
      key: "on_time",
      label: "On-time delivery",
      value: formatPct(onTime),
      icon: <TargetIcon className="size-5" />,
      tone: "positive",
    },
    {
      key: "due",
      label: "Milestones due (14d)",
      value: String(due),
      icon: <FlagIcon className="size-5" />,
      tone: "info",
    },
  ];
}

/** A compact portfolio snapshot: budget vs spend, average progress, and a link into the portfolio. */
function PortfolioSnapshot({ projects }: { projects: ProjectOut[] }) {
  const budget = totalBudgetMinor(projects);
  const spent = totalSpentMinor(projects);
  const spentPct = budget === 0 ? 0 : Math.round((spent / budget) * 100);
  const avg = averageProgress(projects.filter((p) => p.phase !== "live"));
  // A portfolio can carry more than one currency; the snapshot only totals cleanly when every
  // project shares one, so it names the currency it used rather than silently summing across them.
  const currency = projects[0]?.currency ?? "INR";
  const mixedCurrency = projects.some((p) => p.currency !== currency);

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-col gap-1">
        <span className="text-sm text-muted">
          Committed budget{mixedCurrency ? ` (${currency} only)` : ""}
        </span>
        <span className="display text-3xl font-extrabold tnum leading-none">
          {formatMoney(budget, currency)}
        </span>
      </div>

      <div className="flex flex-col gap-2">
        <div className="flex items-center justify-between text-sm">
          <span className="text-muted">Spent to date</span>
          <span className="font-semibold text-ink tnum">
            {formatMoney(spent, currency)} · {spentPct}%
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
  const projectsState = useAsync(useCallback(getAllProjects, []), []);
  const milestonesState = useAsync(useCallback(() => getAllMilestones(), []), []);

  const orgName =
    user?.organizations.find((o) => o.organization_id === user.organization_id)?.name ??
    user?.organizations[0]?.name ??
    "OpsGrids";

  const projects = useMemo(
    () => (projectsState.status === "success" ? projectsState.data : EMPTY_PROJECTS),
    [projectsState],
  );
  const milestones = useMemo(
    () => (milestonesState.status === "success" ? milestonesState.data : EMPTY_MILESTONES),
    [milestonesState],
  );

  const stats = useMemo(() => buildStats(projects, milestones), [projects, milestones]);
  const health = useMemo(() => healthBreakdown(projects), [projects]);
  const attention = useMemo(() => needsAttention(milestones), [milestones]);

  const ready = projectsState.status === "success" && milestonesState.status === "success";
  const failed = projectsState.status === "error" || milestonesState.status === "error";
  const errorMessage =
    (projectsState.status === "error" && problemMessage(projectsState.error)) ||
    (milestonesState.status === "error" && problemMessage(milestonesState.error)) ||
    "Couldn't load the portfolio.";
  const reload = () => {
    projectsState.reload();
    milestonesState.reload();
  };

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
            <ErrorState
              title="Couldn't load the portfolio"
              message={errorMessage}
              action={<Button onClick={reload}>Try again</Button>}
            />
          </Card>
        ) : null}
        {ready
          ? stats.map((stat, i) => (
              <div key={stat.key} style={{ "--i": i } as CSSProperties}>
                <StatCard label={stat.label} value={stat.value} icon={stat.icon} tone={stat.tone} />
              </div>
            ))
          : null}
        {ready ? (
          <div style={{ "--i": stats.length } as CSSProperties}>
            <KpiCard
              label="Resource utilization"
              value={`${SAMPLE_UTILIZATION_PCT}%`}
              deltaPct={3.1}
              series={SAMPLE_UTILIZATION_SERIES}
              icon={<GaugeIcon className="size-5" />}
              tone="cyan"
              sample={SAMPLE_DATA}
            />
          </div>
        ) : null}
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
            <NeedsAttention milestones={attention} now={today()} />
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
