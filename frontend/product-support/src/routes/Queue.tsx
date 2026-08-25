import { useCallback, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { listTickets, listAgents } from "@/api/client";
import type { Agent, Priority, Ticket, TicketStatus } from "@/api/types";
import { agentById } from "@/data/derive";
import { relativeTime } from "@/lib/format";
import { NOW, slaInfo } from "@/lib/sla";
import { useAsync } from "@atmora/client";
import {
  Avatar,
  Card,
  DataTable,
  EmptyState,
  ErrorState,
  PageHeader,
  PriorityPill,
  SearchInput,
  Skeleton,
  SlaPill,
  TicketStatusPill,
} from "@/ui";
import type { Column } from "@/ui";
import { InboxIcon } from "@atmora/ui";

type StatusFilter = TicketStatus | "all";
type PriorityFilter = Priority | "all";
type AssigneeFilter = string | "all" | "unassigned";
type SortKey = "sla" | "updated" | "priority";

const STATUS_OPTIONS: { value: StatusFilter; label: string }[] = [
  { value: "all", label: "All statuses" },
  { value: "new", label: "New" },
  { value: "open", label: "Open" },
  { value: "pending", label: "Pending" },
  { value: "on_hold", label: "On Hold" },
  { value: "solved", label: "Solved" },
];

const PRIORITY_OPTIONS: { value: PriorityFilter; label: string }[] = [
  { value: "all", label: "All priorities" },
  { value: "urgent", label: "Urgent" },
  { value: "high", label: "High" },
  { value: "normal", label: "Normal" },
  { value: "low", label: "Low" },
];

const SORT_OPTIONS: { value: SortKey; label: string }[] = [
  { value: "sla", label: "SLA — most urgent" },
  { value: "updated", label: "Recently updated" },
  { value: "priority", label: "Priority" },
];

const PRIORITY_RANK: Record<Priority, number> = { urgent: 0, high: 1, normal: 2, low: 3 };

/** A stable empty reference so memo deps don't churn while loading. */
const NO_TICKETS: Ticket[] = [];
const NO_AGENTS: Agent[] = [];

/** A labelled native select styled to match the pill controls. */
function FilterSelect<T extends string>({
  label,
  value,
  onChange,
  children,
}: {
  label: string;
  value: T;
  onChange: (value: T) => void;
  children: ReactNode;
}) {
  return (
    <label className="inline-flex h-11 items-center rounded-full border border-line bg-surface pl-3 pr-1.5">
      <span className="sr-only">{label}</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value as T)}
        className="h-full cursor-pointer rounded-full bg-transparent pr-2 text-sm font-semibold text-ink focus:outline-none"
      >
        {children}
      </select>
    </label>
  );
}

/** Sort tickets by the chosen key. SLA order pushes breached tickets to the top. */
function sortTickets(tickets: Ticket[], key: SortKey): Ticket[] {
  const rows = [...tickets];
  if (key === "updated") {
    return rows.sort((a, b) => b.updated_at.localeCompare(a.updated_at));
  }
  if (key === "priority") {
    return rows.sort(
      (a, b) =>
        PRIORITY_RANK[a.priority] - PRIORITY_RANK[b.priority] ||
        b.updated_at.localeCompare(a.updated_at),
    );
  }
  // SLA urgency: live clocks first (soonest deadline, so breached tickets lead), then the rest.
  return rows.sort((a, b) => {
    const am = slaInfo(a).minutes;
    const bm = slaInfo(b).minutes;
    if (am === null && bm === null) return b.updated_at.localeCompare(a.updated_at);
    if (am === null) return 1;
    if (bm === null) return -1;
    return am - bm;
  });
}

function AssigneeCell({ agent }: { agent: Agent | null }) {
  if (!agent) {
    return <span className="text-sm font-medium text-faint">Unassigned</span>;
  }
  return (
    <span className="inline-flex items-center gap-2">
      <Avatar name={agent.name} size="sm" />
      <span className="truncate text-sm text-ink-soft">{agent.name}</span>
    </span>
  );
}

export function QueuePage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [search, setSearch] = useState(() => searchParams.get("q") ?? "");
  const [status, setStatus] = useState<StatusFilter>("all");
  const [priority, setPriority] = useState<PriorityFilter>("all");
  const [assignee, setAssignee] = useState<AssigneeFilter>("all");
  const [sort, setSort] = useState<SortKey>("sla");

  // Seed the search from the ?q= param when arriving from the sidebar / overview search.
  useEffect(() => {
    const q = searchParams.get("q");
    if (q !== null) setSearch(q);
  }, [searchParams]);

  const agentsState = useAsync(listAgents, []);
  const agents = useMemo(
    () => (agentsState.status === "success" ? agentsState.data : NO_AGENTS),
    [agentsState],
  );

  const loader = useCallback(
    () => listTickets({ search, status, priority, assigneeId: assignee }),
    [search, status, priority, assignee],
  );
  const ticketsState = useAsync(loader, [search, status, priority, assignee]);

  const rows = useMemo(() => {
    const items = ticketsState.status === "success" ? ticketsState.data.items : NO_TICKETS;
    return sortTickets(items, sort);
  }, [ticketsState, sort]);

  const total = ticketsState.status === "success" ? ticketsState.data.total : 0;

  const columns: Column<Ticket>[] = useMemo(
    () => [
      {
        key: "ticket",
        header: "Ticket",
        render: (t) => (
          <div className="flex min-w-0 max-w-xs flex-col leading-tight">
            <span className="truncate font-semibold text-ink">{t.subject}</span>
            <span className="text-xs text-muted tnum">{t.id}</span>
          </div>
        ),
      },
      {
        key: "requester",
        header: "Requester",
        headerClassName: "hidden md:table-cell",
        cellClassName: "hidden md:table-cell",
        render: (t) => (
          <div className="flex items-center gap-2.5">
            <Avatar name={t.requester.name} size="sm" />
            <div className="flex min-w-0 flex-col leading-tight">
              <span className="truncate text-sm font-semibold text-ink">{t.requester.name}</span>
              <span className="truncate text-xs text-muted">{t.requester.company}</span>
            </div>
          </div>
        ),
      },
      {
        key: "priority",
        header: "Priority",
        render: (t) => <PriorityPill priority={t.priority} />,
      },
      {
        key: "status",
        header: "Status",
        headerClassName: "hidden sm:table-cell",
        cellClassName: "hidden sm:table-cell",
        render: (t) => <TicketStatusPill status={t.status} />,
      },
      {
        key: "assignee",
        header: "Assignee",
        headerClassName: "hidden lg:table-cell",
        cellClassName: "hidden lg:table-cell",
        render: (t) => <AssigneeCell agent={agentById(agents, t.assignee_id)} />,
      },
      {
        key: "sla",
        header: "SLA",
        render: (t) => <SlaPill ticket={t} />,
      },
      {
        key: "updated",
        header: "Updated",
        align: "right",
        headerClassName: "hidden xl:table-cell",
        cellClassName: "hidden xl:table-cell text-sm text-muted tnum",
        render: (t) => relativeTime(t.updated_at, NOW),
      },
    ],
    [agents],
  );

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Support"
        title="Queue"
        actions={
          <span className="inline-flex h-11 items-center gap-2 rounded-full bg-surface px-4 text-sm font-semibold shadow-card">
            <InboxIcon className="size-4 text-muted" />
            <span className="tnum">{total}</span>
            <span className="font-medium text-muted">tickets</span>
          </span>
        }
      />

      {/* Controls */}
      <Card className="flex flex-col gap-3 p-4 sm:p-5 lg:flex-row lg:items-center">
        <SearchInput
          label="Search tickets"
          placeholder="Search subject, ID, requester or company…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          containerClassName="lg:max-w-sm"
        />
        <div className="flex flex-wrap items-center gap-2 lg:ml-auto">
          <FilterSelect label="Filter by status" value={status} onChange={setStatus}>
            {STATUS_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </FilterSelect>
          <FilterSelect label="Filter by priority" value={priority} onChange={setPriority}>
            {PRIORITY_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </FilterSelect>
          <FilterSelect label="Filter by assignee" value={assignee} onChange={setAssignee}>
            <option value="all">All assignees</option>
            <option value="unassigned">Unassigned</option>
            {agents.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
          </FilterSelect>
          <FilterSelect label="Sort by" value={sort} onChange={setSort}>
            {SORT_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </FilterSelect>
        </div>
      </Card>

      {/* Table */}
      <Card className="p-2 sm:p-4">
        {ticketsState.status === "loading" ? (
          <div className="flex flex-col gap-2 p-2">
            {Array.from({ length: 8 }).map((_, i) => (
              <Skeleton key={i} className="h-14 w-full rounded-tile" />
            ))}
          </div>
        ) : null}
        {ticketsState.status === "error" ? (
          <ErrorState message={ticketsState.error.message} />
        ) : null}
        {ticketsState.status === "success" ? (
          <DataTable
            columns={columns}
            rows={rows}
            rowKey={(t) => t.id}
            caption="Support ticket queue"
            onRowClick={(t) => navigate(`/queue/${t.id}`)}
            emptyState={
              <EmptyState
                icon={<InboxIcon className="size-6" />}
                title="No tickets match"
                description="Nothing matches this search and filter set. Clear a filter to widen the queue."
              />
            }
          />
        ) : null}
      </Card>
    </div>
  );
}
