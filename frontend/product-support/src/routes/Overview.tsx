import { useMemo, useState } from "react";
import type { CSSProperties } from "react";
import { Link, useNavigate } from "react-router-dom";

import { getAllTickets, listAgents } from "@/api/client";
import type { Agent, Ticket } from "@/api/types";
import { useAuth } from "@/auth/context";
import {
  agentById,
  breachingCount,
  csatPct,
  medianFirstResponseMin,
  needsAttentionList,
  openCount,
  priorityBreakdown,
  statusBreakdown,
} from "@/data/derive";
import { KPI_DELTA, KPI_TREND, TICKET_VOLUME } from "@/data/samples";
import { formatMinutes } from "@/lib/format";
import { useAsync } from "@/lib/useAsync";
import {
  Avatar,
  Button,
  Card,
  CategoryBreakdown,
  ErrorState,
  KpiCard,
  PriorityPill,
  SectionHeader,
  Skeleton,
  SlaPill,
  TicketVolumeChart,
} from "@/ui";
import type { KpiTone } from "@/ui";
import {
  AlertTriangleIcon,
  CalendarIcon,
  CheckCircleIcon,
  ChevronRightIcon,
  ClockIcon,
  InboxIcon,
  PlusIcon,
  SearchIcon,
} from "@/ui/icons";

/** A stable empty reference so derived-data memos don't churn while loading. */
const EMPTY_TICKETS: Ticket[] = [];
const EMPTY_AGENTS: Agent[] = [];

/** Colour keys for the two breakdowns — each category keeps a stable, meaningful hue. */
const PRIORITY_COLORS: Record<string, string> = {
  urgent: "var(--color-danger)",
  high: "var(--color-late)",
  normal: "var(--color-info)",
  low: "var(--color-faint)",
};
const STATUS_COLORS: Record<string, string> = {
  new: "var(--color-cyan)",
  open: "var(--color-info)",
  pending: "var(--color-late)",
  on_hold: "var(--color-faint)",
  solved: "var(--color-present)",
};

/** The Overview's top bar: breadcrumb + title, then search / date / new-ticket. */
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
        <h1 className="display text-3xl font-extrabold leading-tight sm:text-[2.1rem]">
          Overview
        </h1>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            const q = query.trim();
            navigate(q ? `/queue?q=${encodeURIComponent(q)}` : "/queue");
          }}
          className="hidden h-11 min-w-0 items-center gap-2 rounded-full bg-surface px-4 shadow-card focus-within:outline focus-within:outline-2 focus-within:outline-accent focus-within:outline-offset-2 md:flex md:w-60"
        >
          <SearchIcon className="size-[18px] shrink-0 text-faint" />
          <label htmlFor="overview-search" className="sr-only">
            Search tickets
          </label>
          <input
            id="overview-search"
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search tickets…"
            className="min-w-0 flex-1 bg-transparent text-sm text-ink placeholder:text-faint focus:outline-none"
          />
        </form>

        <span className="hidden h-11 items-center gap-2 rounded-full bg-surface px-4 text-sm font-semibold text-ink-soft shadow-card sm:inline-flex">
          <CalendarIcon className="size-4 text-muted" />
          <span className="tnum">Last 7 days · to 24 Aug</span>
        </span>

        <Button
          size="md"
          leadingIcon={<PlusIcon className="size-4" />}
          onClick={() => navigate("/queue")}
        >
          New ticket
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
  invertDelta: boolean;
}

function buildKpis(tickets: Ticket[]): KpiConfig[] {
  const csat = csatPct(tickets);
  return [
    {
      key: "open",
      label: "Open tickets",
      value: String(openCount(tickets)),
      deltaPct: KPI_DELTA.open,
      series: [...KPI_TREND.open],
      icon: <InboxIcon className="size-5" />,
      tone: "accent",
      invertDelta: true,
    },
    {
      key: "breaching",
      label: "Breaching SLA",
      value: String(breachingCount(tickets)),
      deltaPct: KPI_DELTA.breaching,
      series: [...KPI_TREND.breaching],
      icon: <AlertTriangleIcon className="size-5" />,
      tone: "warning",
      invertDelta: true,
    },
    {
      key: "frt",
      label: "First-response time",
      value: formatMinutes(medianFirstResponseMin(tickets)),
      deltaPct: KPI_DELTA.firstResponse,
      series: [...KPI_TREND.firstResponse],
      icon: <ClockIcon className="size-5" />,
      tone: "info",
      invertDelta: true,
    },
    {
      key: "csat",
      label: "CSAT",
      value: csat === null ? "—" : `${csat}%`,
      deltaPct: KPI_DELTA.csat,
      series: [...KPI_TREND.csat],
      icon: <CheckCircleIcon className="size-5" />,
      tone: "positive",
      invertDelta: false,
    },
  ];
}

/** One row in the "needs attention" list. */
function AttentionRow({ ticket, agent }: { ticket: Ticket; agent: Agent | null }) {
  return (
    <Link
      to={`/queue/${ticket.id}`}
      className="group flex items-center gap-3 rounded-tile px-3 py-2.5 transition-colors hover:bg-ground"
    >
      <PriorityPill priority={ticket.priority} />
      <div className="flex min-w-0 flex-1 flex-col leading-tight">
        <span className="truncate font-semibold text-ink">{ticket.subject}</span>
        <span className="truncate text-xs text-muted">
          <span className="tnum">{ticket.id}</span> · {ticket.requester.company}
        </span>
      </div>
      <div className="hidden items-center gap-2 sm:flex">
        {agent ? (
          <>
            <Avatar name={agent.name} size="sm" />
            <span className="hidden text-sm text-ink-soft lg:inline">{agent.name}</span>
          </>
        ) : (
          <span className="text-sm font-medium text-faint">Unassigned</span>
        )}
      </div>
      <SlaPill ticket={ticket} />
      <ChevronRightIcon className="size-4 shrink-0 text-faint transition-transform group-hover:translate-x-0.5" />
    </Link>
  );
}

export function OverviewPage() {
  const { user } = useAuth();
  const ticketsState = useAsync(getAllTickets, []);
  const agentsState = useAsync(listAgents, []);

  const orgName =
    user?.organizations.find((o) => o.organization_id === user.organization_id)?.name ??
    user?.organizations[0]?.name ??
    "Atmora";

  const tickets = useMemo(
    () => (ticketsState.status === "success" ? ticketsState.data.items : EMPTY_TICKETS),
    [ticketsState],
  );
  const agents = useMemo(
    () => (agentsState.status === "success" ? agentsState.data : EMPTY_AGENTS),
    [agentsState],
  );

  const kpis = useMemo(() => buildKpis(tickets), [tickets]);
  const priority = useMemo(() => priorityBreakdown(tickets), [tickets]);
  const status = useMemo(() => statusBreakdown(tickets), [tickets]);
  const attention = useMemo(() => needsAttentionList(tickets), [tickets]);

  const loading = ticketsState.status === "loading";
  const errored = ticketsState.status === "error";

  return (
    <div className="rise flex flex-col gap-5">
      <OverviewHeader orgName={orgName} />

      {/* Row 1 — KPI cards */}
      <section
        aria-label="Key metrics"
        className="cascade grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4"
      >
        {loading
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
        {errored ? (
          <Card className="p-5 sm:col-span-2 xl:col-span-4">
            <ErrorState message={ticketsState.error.message} />
          </Card>
        ) : null}
        {ticketsState.status === "success"
          ? kpis.map((kpi, i) => (
              <div key={kpi.key} style={{ "--i": i } as CSSProperties}>
                <KpiCard
                  label={kpi.label}
                  value={kpi.value}
                  deltaPct={kpi.deltaPct}
                  series={kpi.series}
                  icon={kpi.icon}
                  tone={kpi.tone}
                  invertDelta={kpi.invertDelta}
                />
              </div>
            ))
          : null}
      </section>

      {/* Row 2 — volume · priority · status */}
      <section aria-label="Ticket trends" className="grid grid-cols-1 gap-5 xl:grid-cols-4">
        <Card className="flex flex-col gap-5 p-6 xl:col-span-2">
          <SectionHeader title="Ticket volume" subtitle="Opened vs solved, last 7 days" />
          <TicketVolumeChart data={TICKET_VOLUME} />
        </Card>

        <Card className="flex flex-col gap-5 p-6 xl:col-span-1">
          <SectionHeader title="By priority" subtitle="Open queue" />
          {ticketsState.status === "success" ? (
            <CategoryBreakdown
              slices={priority}
              colors={PRIORITY_COLORS}
              totalNoun="tickets in the open queue"
            />
          ) : (
            <Skeleton className="h-56 w-full rounded-tile" />
          )}
        </Card>

        <Card className="flex flex-col gap-5 p-6 xl:col-span-1">
          <SectionHeader title="By status" subtitle="All tickets" />
          {ticketsState.status === "success" ? (
            <CategoryBreakdown
              slices={status}
              colors={STATUS_COLORS}
              totalNoun="tickets in total"
            />
          ) : (
            <Skeleton className="h-56 w-full rounded-tile" />
          )}
        </Card>
      </section>

      {/* Row 3 — needs attention */}
      <section aria-label="Needs attention">
        <Card className="flex flex-col gap-4 p-6">
          <SectionHeader
            title="Needs attention"
            subtitle="Breached, or due within the hour — work these first"
            action={
              <Link
                to="/queue"
                className="text-sm font-semibold text-accent hover:text-accent-strong transition-colors"
              >
                Open queue
              </Link>
            }
          />
          {loading ? (
            <div className="flex flex-col gap-2">
              {Array.from({ length: 5 }).map((_, i) => (
                <Skeleton key={i} className="h-14 w-full rounded-tile" />
              ))}
            </div>
          ) : null}
          {errored ? <ErrorState message={ticketsState.error.message} /> : null}
          {ticketsState.status === "success" ? (
            attention.length === 0 ? (
              <div className="flex flex-col items-center gap-2 py-10 text-center">
                <span className="grid size-12 place-items-center rounded-full bg-present-soft text-present">
                  <CheckCircleIcon className="size-6" />
                </span>
                <p className="text-base font-bold">Everything is within SLA</p>
                <p className="text-sm text-muted">
                  No open ticket is breached or due within the hour. Nice work.
                </p>
              </div>
            ) : (
              <ul className="-mx-3 flex flex-col">
                {attention.map((ticket) => (
                  <li key={ticket.id}>
                    <AttentionRow ticket={ticket} agent={agentById(agents, ticket.assignee_id)} />
                  </li>
                ))}
              </ul>
            )
          ) : null}
        </Card>
      </section>
    </div>
  );
}
