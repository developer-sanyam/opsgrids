import { useCallback, useMemo, useState } from "react";
import type { CSSProperties, ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";

import { getAllAssets, getAllIncidents, getAllRequests, TRENDS_ARE_SAMPLE } from "@/api/client";
import type { Asset, Incident, ServiceRequest } from "@/api/types";
import { useAuth } from "@opsgrids/client";
import {
  assetsUnderManagement,
  breakdownByCategory,
  breakdownByPriority,
  needsAttention,
  openIncidentCount,
  requestsAwaitingApproval,
  slaCompliancePct,
} from "@/data/derive";
import { KPI_SAMPLE } from "@/data/samples";
import { useAsync } from "@opsgrids/client";
import {
  Button,
  Card,
  CategoryDonut,
  ErrorState,
  IncidentVolumeChart,
  KpiCard,
  NeedsAttention,
  PriorityBreakdown,
  SectionHeader,
  Skeleton,
} from "@/ui";
import type { KpiTone } from "@/ui";
import {
  AlertTriangleIcon,
  BellIcon,
  BoxIcon,
  CalendarIcon,
  CheckCircleIcon,
  PlusIcon,
  SearchIcon,
  ShieldCheckIcon,
  TicketIcon,
} from "@opsgrids/ui";
import { SAMPLE_TREND } from "@/ui/sampleCopy";

interface OverviewData {
  incidents: Incident[];
  requests: ServiceRequest[];
  assets: Asset[];
}

const WARRANTY_WINDOW_DAYS = 60;
const NOW_MS = Date.parse("2026-08-24T18:00:00Z");

/** Assets whose warranty / support expires within the next 60 days (and hasn't already lapsed). */
function warrantyExpiringSoon(assets: readonly Asset[]): number {
  const horizon = NOW_MS + WARRANTY_WINDOW_DAYS * 86_400_000;
  return assets.filter((a) => {
    if (a.status === "retired" || a.warranty_expires_at === null) return false;
    const exp = Date.parse(`${a.warranty_expires_at}T00:00:00Z`);
    return exp >= NOW_MS && exp <= horizon;
  }).length;
}

/** The Overview's top bar: breadcrumb + title, then search, notifications and the primary action. */
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
          className="hidden h-11 min-w-0 items-center gap-2 rounded-full bg-surface px-4 shadow-card focus-within:outline focus-within:outline-2 focus-within:outline-accent focus-within:outline-offset-2 md:flex md:w-60"
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

        <Link
          to="/notifications"
          aria-label="Notifications"
          className="relative grid size-11 shrink-0 place-items-center rounded-full bg-surface text-ink-soft shadow-card hover:bg-ground transition-colors"
        >
          <BellIcon className="size-5" />
          <span className="absolute right-2.5 top-2.5 size-2 rounded-full bg-accent ring-2 ring-surface" aria-hidden />
        </Link>

        <span className="hidden h-11 items-center gap-2 rounded-full bg-surface px-4 text-sm font-semibold text-ink-soft shadow-card sm:inline-flex">
          <CalendarIcon className="size-4 text-muted" />
          <span className="tnum">Mon, 24 Aug 2026</span>
        </span>

        <Button
          size="md"
          leadingIcon={<PlusIcon className="size-4" />}
          onClick={() => navigate("/incidents")}
        >
          New incident
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
  icon: ReactNode;
  tone: KpiTone;
  goodWhen: "up" | "down";
  sample: boolean;
}

function buildKpis(data: OverviewData): KpiConfig[] {
  return [
    {
      key: "open",
      label: "Open incidents",
      value: String(openIncidentCount(data.incidents)),
      deltaPct: KPI_SAMPLE.openIncidents.delta,
      series: KPI_SAMPLE.openIncidents.series,
      icon: <AlertTriangleIcon className="size-5" />,
      tone: "danger",
      goodWhen: "down",
      sample: true,
    },
    {
      key: "sla",
      label: "SLA compliance",
      value: `${slaCompliancePct(data.incidents)}%`,
      deltaPct: KPI_SAMPLE.slaCompliance.delta,
      series: KPI_SAMPLE.slaCompliance.series,
      icon: <ShieldCheckIcon className="size-5" />,
      tone: "positive",
      goodWhen: "up",
      sample: true,
    },
    {
      key: "approval",
      label: "Requests awaiting approval",
      value: String(requestsAwaitingApproval(data.requests)),
      deltaPct: KPI_SAMPLE.awaitingApproval.delta,
      series: KPI_SAMPLE.awaitingApproval.series,
      icon: <TicketIcon className="size-5" />,
      tone: "info",
      goodWhen: "down",
      sample: true,
    },
    {
      key: "assets",
      label: "Assets under management",
      value: String(assetsUnderManagement(data.assets)),
      deltaPct: KPI_SAMPLE.assetsManaged.delta,
      series: KPI_SAMPLE.assetsManaged.series,
      icon: <BoxIcon className="size-5" />,
      tone: "cyan",
      goodWhen: "up",
      sample: true,
    },
  ];
}

/** One row in the compact "desk snapshot" panel. */
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
  const load = useCallback(
    () =>
      Promise.all([getAllIncidents(), getAllRequests(), getAllAssets()]).then(
        ([incidents, requests, assets]): OverviewData => ({ incidents, requests, assets }),
      ),
    [],
  );
  const state = useAsync(load, []);

  const orgName =
    user?.organizations.find((o) => o.organization_id === user.organization_id)?.name ??
    user?.organizations[0]?.name ??
    "OpsGrids";

  const kpis = useMemo(
    () => (state.status === "success" ? buildKpis(state.data) : []),
    [state],
  );
  const priority = useMemo(
    () => (state.status === "success" ? breakdownByPriority(state.data.incidents) : []),
    [state],
  );
  const category = useMemo(
    () => (state.status === "success" ? breakdownByCategory(state.data.incidents) : []),
    [state],
  );
  const attention = useMemo(
    () => (state.status === "success" ? needsAttention(state.data.incidents) : []),
    [state],
  );

  const openTotal = state.status === "success" ? openIncidentCount(state.data.incidents) : 0;

  return (
    <div className="rise flex flex-col gap-5">
      <OverviewHeader orgName={orgName} />

      {/* Row 1 — KPI cards */}
      <section
        aria-label="Key metrics"
        className="cascade grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4"
      >
        {state.status === "loading"
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
                  goodWhen={kpi.goodWhen}
                  sample={kpi.sample ? SAMPLE_TREND : false}
                />
              </div>
            ))
          : null}
      </section>

      {/* Row 2 — volume trend · priority bars · category donut */}
      <section aria-label="Incident analytics" className="grid grid-cols-1 gap-5 xl:grid-cols-4">
        <Card className="flex flex-col gap-4 p-6 xl:col-span-2">
          <SectionHeader title="Incident volume" subtitle="Opened vs resolved over time" />
          {state.status === "success" ? (
            <IncidentVolumeChart sample={TRENDS_ARE_SAMPLE} />
          ) : state.status === "error" ? (
            <ErrorState message={state.error.message} />
          ) : (
            <Skeleton className="h-56 w-full rounded-tile" />
          )}
        </Card>

        <Card className="flex flex-col gap-5 p-6 xl:col-span-1">
          <SectionHeader title="Open by priority" subtitle="Where the load sits" />
          {state.status === "success" && priority.length > 0 ? (
            <PriorityBreakdown data={priority} total={openTotal} />
          ) : state.status === "error" ? (
            <ErrorState message={state.error.message} />
          ) : (
            <Skeleton className="h-56 w-full rounded-tile" />
          )}
        </Card>

        <Card className="flex flex-col gap-5 p-6 xl:col-span-1">
          <SectionHeader title="Open by category" subtitle="What's breaking" />
          {state.status === "success" && category.length > 0 ? (
            <CategoryDonut data={category} total={openTotal} />
          ) : state.status === "error" ? (
            <ErrorState message={state.error.message} />
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
          {state.status === "loading" ? (
            <div className="flex flex-col gap-3 py-2">
              {Array.from({ length: 4 }).map((_, i) => (
                <Skeleton key={i} className="h-16 w-full rounded-tile" />
              ))}
            </div>
          ) : null}
          {state.status === "error" ? <ErrorState message={state.error.message} /> : null}
          {state.status === "success" ? <NeedsAttention incidents={attention} /> : null}
        </Card>

        <Card className="flex flex-col gap-5 p-6">
          <SectionHeader title="Desk snapshot" subtitle="Right now" />
          {state.status === "success" ? (
            <ul className="flex flex-col gap-4">
              <SnapshotRow
                icon={<AlertTriangleIcon className="size-5" />}
                label="Open P1 incidents"
                value={String(
                  state.data.incidents.filter(
                    (i) => i.priority === "P1" && i.status !== "resolved" && i.status !== "closed",
                  ).length,
                )}
                tone="bg-danger-soft text-danger"
              />
              <SnapshotRow
                icon={<ShieldCheckIcon className="size-5" />}
                label="SLAs breached"
                value={String(state.data.incidents.filter((i) => i.sla_state === "breached").length)}
                tone="bg-late-soft text-late"
              />
              <SnapshotRow
                icon={<TicketIcon className="size-5" />}
                label="Requests awaiting approval"
                value={String(requestsAwaitingApproval(state.data.requests))}
                tone="bg-info-soft text-info"
              />
              <SnapshotRow
                icon={<BoxIcon className="size-5" />}
                label="Warranties expiring ≤ 60d"
                value={String(warrantyExpiringSoon(state.data.assets))}
                tone="bg-accent-soft text-accent-ink"
              />
              <SnapshotRow
                icon={<CheckCircleIcon className="size-5" />}
                label="Resolved / closed today"
                value={String(
                  state.data.incidents.filter(
                    (i) =>
                      (i.status === "resolved" || i.status === "closed") &&
                      i.updated_at.startsWith("2026-08-24"),
                  ).length,
                )}
                tone="bg-present-soft text-present"
              />
            </ul>
          ) : (
            <div className="flex flex-col gap-4">
              {Array.from({ length: 5 }).map((_, i) => (
                <Skeleton key={i} className="h-9 w-full rounded-tile" />
              ))}
            </div>
          )}
        </Card>
      </section>
    </div>
  );
}
