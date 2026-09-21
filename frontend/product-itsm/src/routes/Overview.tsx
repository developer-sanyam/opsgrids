import { useCallback, useMemo, useState } from "react";
import type { CSSProperties, ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";

import { getOverview, listIncidents } from "@/api/client";
import type { Incident, Overview } from "@/api/types";
import { useAsync, useAuth } from "@opsgrids/client";
import { needsAttention } from "@/data/derive";
import { todayLabel } from "@/lib/format";
import {
  Button,
  Card,
  CategoryDonut,
  ErrorState,
  KpiCard,
  NeedsAttention,
  PriorityBreakdown,
  SectionHeader,
  Skeleton,
} from "@/ui";
import { IncidentVolumeChart } from "@/ui/IncidentVolumeChart";
import {
  AlertTriangleIcon,
  BoxIcon,
  CalendarIcon,
  PlusIcon,
  SearchIcon,
  ShieldCheckIcon,
  TicketIcon,
} from "@opsgrids/ui";

/** The Overview's top bar: breadcrumb + title, then search, the date and the primary action. */
function OverviewHeader({ orgName }: { orgName: string }) {
  const navigate = useNavigate();
  const [query, setQuery] = useState("");

  return (
    <header className="flex flex-col gap-4 xl:flex-row xl:items-end xl:justify-between">
      <div className="flex flex-col gap-1">
        <nav aria-label="Breadcrumb" className="text-sm font-medium text-muted">
          Service desk <span className="text-faint">/</span>{" "}
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
            navigate(q ? `/incidents?q=${encodeURIComponent(q)}` : "/incidents");
          }}
          className="flex h-11 min-w-0 flex-1 items-center gap-2 rounded-full bg-surface px-4 shadow-card focus-within:outline focus-within:outline-2 focus-within:outline-accent focus-within:outline-offset-2 sm:w-60 sm:flex-none"
        >
          <SearchIcon className="size-[18px] shrink-0 text-faint" />
          <label htmlFor="overview-search" className="sr-only">
            Search incidents
          </label>
          <input
            id="overview-search"
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search incidents…"
            className="min-w-0 flex-1 bg-transparent text-sm text-ink placeholder:text-faint focus:outline-none"
          />
        </form>

        <span className="hidden h-11 items-center gap-2 rounded-full bg-surface px-4 text-sm font-semibold text-ink-soft shadow-card sm:inline-flex">
          <CalendarIcon className="size-4 text-muted" />
          <span className="tnum">{todayLabel()}</span>
        </span>

        <Button
          size="md"
          leadingIcon={<PlusIcon className="size-4" />}
          onClick={() => navigate("/incidents?new=1")}
        >
          New incident
        </Button>
      </div>
    </header>
  );
}

/** One row in the compact "desk snapshot" panel — everything here is exact, from the overview
 *  rollup, never recomputed from a capped page. */
function SnapshotRow({
  icon,
  label,
  value,
  tone,
}: {
  icon: ReactNode;
  label: string;
  value: string;
  tone: string;
}) {
  return (
    <li className="flex items-center gap-3">
      <span className={`grid size-9 shrink-0 place-items-center rounded-tile ${tone}`}>{icon}</span>
      <span className="text-sm text-ink-soft">{label}</span>
      <span className="ml-auto display text-xl font-extrabold tnum">{value}</span>
    </li>
  );
}

export function OverviewPage() {
  const { user } = useAuth();
  const overviewState = useAsync<Overview>(useCallback(() => getOverview(), []), []);
  // A bounded, priority-sorted slice powers the "needs attention" preview — a curated queue with
  // a "View all" link beside it, not a count, so it does not need every row in the org.
  const attentionState = useAsync<Incident[]>(
    useCallback(async () => (await listIncidents({ sort: "priority", size: 50 })).items, []),
    [],
  );

  const orgName =
    user?.organizations.find((o) => o.organization_id === user.organization_id)?.name ??
    user?.organizations[0]?.name ??
    "OpsGrids";

  const attention = useMemo(
    () => (attentionState.status === "success" ? needsAttention(attentionState.data) : []),
    [attentionState],
  );

  const p1Count =
    overviewState.status === "success"
      ? (overviewState.data.by_priority.find((b) => b.key === "P1")?.count ?? 0)
      : 0;

  return (
    <div className="rise flex flex-col gap-5">
      <OverviewHeader orgName={orgName} />

      {/* Row 1 — KPI cards */}
      <section
        aria-label="Key metrics"
        className="cascade grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4"
      >
        {overviewState.status === "loading"
          ? Array.from({ length: 4 }).map((_, i) => (
              <Card key={i} className="flex flex-col gap-4 p-5" style={{ "--i": i } as CSSProperties}>
                <div className="flex justify-between">
                  <Skeleton className="size-10 rounded-tile" />
                  <Skeleton className="h-8 w-20" />
                </div>
                <Skeleton className="h-4 w-28" />
                <Skeleton className="h-9 w-20 rounded-tile" />
              </Card>
            ))
          : null}
        {overviewState.status === "error" ? (
          <Card className="p-5 sm:col-span-2 xl:col-span-4">
            <ErrorState
              message={overviewState.error.message}
              onRetry={overviewState.reload}
            />
          </Card>
        ) : null}
        {overviewState.status === "success"
          ? (() => {
              const o = overviewState.data;
              return (
                <>
                  <div style={{ "--i": 0 } as CSSProperties}>
                    <KpiCard
                      label="Open incidents"
                      value={String(o.open_incidents)}
                      icon={<AlertTriangleIcon className="size-5" />}
                      tone="danger"
                      goodWhen="down"
                    />
                  </div>
                  <div style={{ "--i": 1 } as CSSProperties}>
                    <KpiCard
                      label="SLA compliance"
                      value={`${o.sla_compliance_pct}%`}
                      icon={<ShieldCheckIcon className="size-5" />}
                      tone="positive"
                      goodWhen="up"
                    />
                  </div>
                  <div style={{ "--i": 2 } as CSSProperties}>
                    <KpiCard
                      label="Requests awaiting approval"
                      value={String(o.requests_awaiting_approval)}
                      icon={<TicketIcon className="size-5" />}
                      tone="info"
                      goodWhen="down"
                    />
                  </div>
                  <div style={{ "--i": 3 } as CSSProperties}>
                    <KpiCard
                      label="Assets under management"
                      value={String(o.assets_under_management)}
                      icon={<BoxIcon className="size-5" />}
                      tone="cyan"
                      goodWhen="up"
                    />
                  </div>
                </>
              );
            })()
          : null}
      </section>

      {/* Row 2 — volume trend · priority bars · category donut */}
      <section aria-label="Incident analytics" className="grid grid-cols-1 gap-5 xl:grid-cols-4">
        <Card className="flex flex-col gap-4 p-6 xl:col-span-2">
          <SectionHeader title="Incident volume" subtitle="Opened vs resolved, last 6 weeks" />
          {overviewState.status === "success" ? (
            <IncidentVolumeChart data={overviewState.data.volume} />
          ) : overviewState.status === "error" ? (
            <ErrorState message={overviewState.error.message} onRetry={overviewState.reload} />
          ) : (
            <Skeleton className="h-56 w-full rounded-tile" />
          )}
        </Card>

        <Card className="flex flex-col gap-5 p-6 xl:col-span-1">
          <SectionHeader title="Open by priority" subtitle="Where the load sits" />
          {overviewState.status === "success" && overviewState.data.by_priority.length > 0 ? (
            <PriorityBreakdown data={overviewState.data.by_priority} total={overviewState.data.open_incidents} />
          ) : overviewState.status === "error" ? (
            <ErrorState message={overviewState.error.message} onRetry={overviewState.reload} />
          ) : overviewState.status === "success" ? (
            <p className="text-sm text-muted">No open incidents right now.</p>
          ) : (
            <Skeleton className="h-56 w-full rounded-tile" />
          )}
        </Card>

        <Card className="flex flex-col gap-5 p-6 xl:col-span-1">
          <SectionHeader title="Open by category" subtitle="What's breaking" />
          {overviewState.status === "success" && overviewState.data.by_category.length > 0 ? (
            <CategoryDonut data={overviewState.data.by_category} total={overviewState.data.open_incidents} />
          ) : overviewState.status === "error" ? (
            <ErrorState message={overviewState.error.message} onRetry={overviewState.reload} />
          ) : overviewState.status === "success" ? (
            <p className="text-sm text-muted">No open incidents right now.</p>
          ) : (
            <Skeleton className="h-56 w-full rounded-tile" />
          )}
        </Card>
      </section>

      {/* Row 3 — needs attention · desk snapshot */}
      <section aria-label="Escalations" className="grid grid-cols-1 gap-5 xl:grid-cols-3 items-start">
        <Card className="flex flex-col gap-4 p-6 xl:col-span-2">
          <SectionHeader
            title="Needs attention"
            subtitle="P1 incidents and anything breaching SLA"
            action={
              <Link
                to="/incidents?priority=P1"
                className="text-sm font-semibold text-accent hover:text-accent-strong transition-colors"
              >
                View all
              </Link>
            }
          />
          {attentionState.status === "loading" ? (
            <div className="flex flex-col gap-3 py-2">
              {Array.from({ length: 4 }).map((_, i) => (
                <Skeleton key={i} className="h-16 w-full rounded-tile" />
              ))}
            </div>
          ) : null}
          {attentionState.status === "error" ? (
            <ErrorState message={attentionState.error.message} onRetry={attentionState.reload} />
          ) : null}
          {attentionState.status === "success" ? <NeedsAttention incidents={attention} /> : null}
        </Card>

        <Card className="flex flex-col gap-5 p-6">
          <SectionHeader title="Desk snapshot" subtitle="Right now" />
          {overviewState.status === "success" ? (
            <ul className="flex flex-col gap-4">
              <SnapshotRow
                icon={<AlertTriangleIcon className="size-5" />}
                label="Open P1 incidents"
                value={String(p1Count)}
                tone="bg-danger-soft text-danger"
              />
              <SnapshotRow
                icon={<ShieldCheckIcon className="size-5" />}
                label="SLA compliance"
                value={`${overviewState.data.sla_compliance_pct}%`}
                tone="bg-late-soft text-late"
              />
              <SnapshotRow
                icon={<TicketIcon className="size-5" />}
                label="Requests awaiting approval"
                value={String(overviewState.data.requests_awaiting_approval)}
                tone="bg-info-soft text-info"
              />
            </ul>
          ) : overviewState.status === "error" ? (
            <ErrorState message={overviewState.error.message} onRetry={overviewState.reload} />
          ) : (
            <div className="flex flex-col gap-4">
              {Array.from({ length: 3 }).map((_, i) => (
                <Skeleton key={i} className="h-9 w-full rounded-tile" />
              ))}
            </div>
          )}
        </Card>
      </section>
    </div>
  );
}
