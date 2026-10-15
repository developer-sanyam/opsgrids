import { useMemo, useState } from "react";
import type { CSSProperties } from "react";
import { Link, useNavigate } from "react-router-dom";

import { getOverview, listAgents, listTickets } from "@/api/client";
import type { Agent, Overview, Ticket } from "@/api/types";
import { Can, orgToday, useAsync, useAuth } from "@opsgrids/client";
import { agentById, needsAttentionList } from "@/data/derive";
import { now as clockNow } from "@/lib/clock";
import { formatDate, formatMinutes } from "@/lib/format";
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
} from "@opsgrids/ui";
import { NewTicketDialog } from "@/ui/NewTicketDialog";

/** A stable empty reference so derived-data memos don't churn while loading. */
const EMPTY_AGENTS: Agent[] = [];
const EMPTY_TICKETS: Ticket[] = [];

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

/** The Overview's top bar: breadcrumb + title, then date / new-ticket. The shell's own header
 *  already has a search that reaches the queue (`AppShell`'s `search.fallback`), so this doesn't
 *  repeat it. */
function OverviewHeader({
  orgName,
  onNewTicket,
}: {
  orgName: string;
  onNewTicket: () => void;
}) {
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
        <span className="hidden h-11 items-center gap-2 rounded-full bg-surface px-4 text-sm font-semibold text-ink-soft shadow-card sm:inline-flex">
          <CalendarIcon className="size-4 text-muted" />
          <span className="tnum">Last 7 days · to {formatDate(orgToday(clockNow()))}</span>
        </span>

        <Can permission="support.desk.ticket.write" scope="ORG">
          <Button size="md" leadingIcon={<PlusIcon className="size-4" />} onClick={onNewTicket}>
            New ticket
          </Button>
        </Can>
      </div>
    </header>
  );
}

interface KpiConfig {
  key: string;
  label: string;
  value: string;
  icon: React.ReactNode;
  tone: KpiTone;
  goodWhen: "up" | "down";
}

/** `GET /support/overview` answers the current snapshot only — no trailing history to compare
 *  against — so every card renders its live figure alone, with no delta and no sparkline. An
 *  invented trend is worse than none. */
function buildKpis(overview: Overview): KpiConfig[] {
  return [
    {
      key: "open",
      label: "Open tickets",
      value: String(overview.open_tickets),
      icon: <InboxIcon className="size-5" />,
      tone: "accent",
      goodWhen: "down",
    },
    {
      key: "breaching",
      label: "Breaching SLA",
      value: String(overview.breaching_sla),
      icon: <AlertTriangleIcon className="size-5" />,
      tone: "warning",
      goodWhen: "down",
    },
    {
      key: "frt",
      label: "First-response time",
      value: formatMinutes(overview.first_response_median_min),
      icon: <ClockIcon className="size-5" />,
      tone: "info",
      goodWhen: "down",
    },
    {
      key: "csat",
      label: "CSAT",
      value: overview.csat_pct === null ? "—" : `${overview.csat_pct}%`,
      icon: <CheckCircleIcon className="size-5" />,
      tone: "positive",
      goodWhen: "up",
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
  const navigate = useNavigate();
  const { user } = useAuth();
  const overviewState = useAsync(getOverview, []);
  const agentsState = useAsync(listAgents, []);
  // "Needs attention" has no rollup of its own on `GET /support/overview` — it names actual
  // tickets, not a count. Bounded to the most severe 50, priority order, which is the best single
  // request can offer without a dedicated backend query; it is a preview, not the full queue (see
  // "Open queue" below for that), and never claims a count the KPI row above does not already own.
  const attentionTicketsState = useAsync(() => listTickets({ sort: "priority", size: 50 }), []);
  const [creating, setCreating] = useState(false);

  const orgName =
    user?.organizations.find((o) => o.organization_id === user.organization_id)?.name ??
    user?.organizations[0]?.name ??
    "OpsGrids";

  const agents = useMemo(
    () => (agentsState.status === "success" ? agentsState.data : EMPTY_AGENTS),
    [agentsState],
  );
  const attentionSource = useMemo(
    () => (attentionTicketsState.status === "success" ? attentionTicketsState.data.items : EMPTY_TICKETS),
    [attentionTicketsState],
  );
  const attention = useMemo(
    () => needsAttentionList(attentionSource).slice(0, 8),
    [attentionSource],
  );

  const overview = overviewState.status === "success" ? overviewState.data : null;
  const kpis = useMemo(() => (overview ? buildKpis(overview) : []), [overview]);

  const loading = overviewState.status === "loading";
  const errored = overviewState.status === "error";

  return (
    <div className="rise flex flex-col gap-5">
      <OverviewHeader orgName={orgName} onNewTicket={() => setCreating(true)} />

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
            <ErrorState
              message={overviewState.error.message}
              action={<Button onClick={overviewState.reload}>Try again</Button>}
            />
          </Card>
        ) : null}
        {overview
          ? kpis.map((kpi, i) => (
              <div key={kpi.key} style={{ "--i": i } as CSSProperties}>
                <KpiCard
                  label={kpi.label}
                  value={kpi.value}
                  icon={kpi.icon}
                  tone={kpi.tone}
                  goodWhen={kpi.goodWhen}
                />
              </div>
            ))
          : null}
      </section>

      {/* Row 2 — volume · priority · status */}
      <section aria-label="Ticket trends" className="grid grid-cols-1 gap-5 xl:grid-cols-4">
        <Card className="flex flex-col gap-5 p-6 xl:col-span-2">
          <SectionHeader title="Ticket volume" subtitle="Opened vs solved, last 7 days" />
          {overview ? (
            <TicketVolumeChart data={overview.volume} />
          ) : (
            <Skeleton className="h-56 w-full rounded-tile" />
          )}
        </Card>

        <Card className="flex flex-col gap-5 p-6 xl:col-span-1">
          <SectionHeader title="By priority" subtitle="Open queue" />
          {overview ? (
            <CategoryBreakdown
              slices={overview.priority_breakdown}
              colors={PRIORITY_COLORS}
              totalNoun="tickets in the open queue"
            />
          ) : (
            <Skeleton className="h-56 w-full rounded-tile" />
          )}
        </Card>

        <Card className="flex flex-col gap-5 p-6 xl:col-span-1">
          <SectionHeader title="By status" subtitle="All tickets" />
          {overview ? (
            <CategoryBreakdown
              slices={overview.status_breakdown}
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
            subtitle="Breached, or due within the hour, among the 50 highest-priority tickets — open the queue for the full list"
            action={
              <Link
                to="/queue"
                className="hit-44 relative text-sm font-semibold text-accent hover:text-accent-strong transition-colors"
              >
                Open queue
              </Link>
            }
          />
          {attentionTicketsState.status === "loading" ? (
            <div className="flex flex-col gap-2">
              {Array.from({ length: 5 }).map((_, i) => (
                <Skeleton key={i} className="h-14 w-full rounded-tile" />
              ))}
            </div>
          ) : null}
          {attentionTicketsState.status === "error" ? (
            <ErrorState
              message={attentionTicketsState.error.message}
              action={<Button onClick={attentionTicketsState.reload}>Try again</Button>}
            />
          ) : null}
          {attentionTicketsState.status === "success" ? (
            attention.length === 0 ? (
              <div className="flex flex-col items-center gap-2 py-10 text-center">
                <span className="grid size-12 place-items-center rounded-full bg-present-soft text-present">
                  <CheckCircleIcon className="size-6" />
                </span>
                <p className="text-base font-bold">Everything is within SLA</p>
                <p className="text-sm text-muted">
                  No ticket in the preview is breached or due within the hour. Nice work.
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

      {creating ? (
        <NewTicketDialog
          onClose={() => setCreating(false)}
          onCreated={(created) => navigate(`/queue/${created.id}`)}
        />
      ) : null}
    </div>
  );
}
