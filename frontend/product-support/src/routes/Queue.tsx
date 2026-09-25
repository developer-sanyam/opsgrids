import { useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { listTickets, listAgents } from "@/api/client";
import type { TicketSort } from "@/api/client";
import type { Agent, Priority, Ticket, TicketStatus } from "@/api/types";
import { agentById } from "@/data/derive";
import { now as clockNow } from "@/lib/clock";
import { relativeTime } from "@/lib/format";
import { Can, useAsync, useCan } from "@opsgrids/client";
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
import { Button, DownloadIcon, InboxIcon, Pagination, PlusIcon } from "@opsgrids/ui";
import type { TableSelection } from "@opsgrids/ui";
import { NewTicketDialog } from "@/ui/NewTicketDialog";
import { downloadCsv, toCsv } from "@/lib/exportCsv";

type StatusFilter = TicketStatus | "all";
type PriorityFilter = Priority | "all";
type AssigneeFilter = string | "all" | "unassigned";

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

// The server's `sort` param fixes each key's direction (`services/support/app/selectors.py:
// SORTS`) — there is no separate ascending/descending toggle to offer.
const SORT_OPTIONS: { value: TicketSort; label: string }[] = [
  { value: "priority", label: "Priority — most severe" },
  { value: "updated", label: "Recently updated" },
  { value: "created", label: "Recently created" },
  { value: "number", label: "Ticket number" },
];

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
    <label
      className={
        "inline-flex h-11 items-center rounded-full border border-line bg-surface pl-3 pr-1.5 " +
        "focus-within:outline focus-within:outline-2 focus-within:outline-accent focus-within:outline-offset-2"
      }
    >
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
  const canWrite = useCan("support.desk.ticket.write", "ORG");
  const [searchParams] = useSearchParams();
  const [search, setSearch] = useState(() => searchParams.get("q") ?? "");
  const [status, setStatus] = useState<StatusFilter>("all");
  const [priority, setPriority] = useState<PriorityFilter>("all");
  const [assignee, setAssignee] = useState<AssigneeFilter>("all");
  const [sort, setSort] = useState<TicketSort>("priority");
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(25);
  const [creating, setCreating] = useState(false);
  const [selectedIds, setSelectedIds] = useState<ReadonlySet<string>>(new Set());

  // Seed the search from the ?q= param when arriving from the sidebar / overview search.
  useEffect(() => {
    const q = searchParams.get("q");
    if (q !== null) setSearch(q);
  }, [searchParams]);

  // A new filter, search or sort is a different query — it starts back at page 1.
  useEffect(() => {
    setPage(1);
  }, [search, status, priority, assignee, sort]);

  const agentsState = useAsync(listAgents, []);
  const agents = useMemo(
    () => (agentsState.status === "success" ? agentsState.data : []),
    [agentsState],
  );

  const ticketsState = useAsync(
    () => listTickets({ search, status, priority, assigneeId: assignee, sort, page, size }),
    [search, status, priority, assignee, sort, page, size],
  );

  const hasFilter =
    search.trim() !== "" || status !== "all" || priority !== "all" || assignee !== "all";

  const columns: Column<Ticket>[] = useMemo(
    () => [
      {
        key: "ticket",
        header: "Ticket",
        mobile: "title",
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
        mobile: "status",
        render: (t) => <PriorityPill priority={t.priority} />,
      },
      {
        key: "status",
        header: "Status",
        mobile: "status",
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
        render: (t) => relativeTime(t.updated_at, clockNow()),
      },
    ],
    [agents],
  );

  const items = ticketsState.status === "success" ? ticketsState.data.items : [];

  const selection: TableSelection<Ticket> = {
    selected: selectedIds,
    onToggle: (key, next) =>
      setSelectedIds((prev) => {
        const nextSet = new Set(prev);
        if (next) nextSet.add(key);
        else nextSet.delete(key);
        return nextSet;
      }),
    onToggleAll: (next) => setSelectedIds(next ? new Set(items.map((t) => t.id)) : new Set()),
    label: (t) => t.subject,
  };

  function exportSelected() {
    const rows = items.filter((t) => selectedIds.has(t.id));
    const csv = toCsv(
      ["ID", "Subject", "Requester", "Priority", "Status", "Updated"],
      rows.map((t) => [t.id, t.subject, t.requester.name, t.priority, t.status, t.updated_at]),
    );
    downloadCsv(`tickets-selected-${rows.length}.csv`, csv);
  }

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Support"
        title="Queue"
        actions={
          <div className="flex items-center gap-2">
            <span className="inline-flex h-11 items-center gap-2 rounded-full bg-surface px-4 text-sm font-semibold shadow-card">
              <InboxIcon className="size-4 text-muted" />
              <span className="tnum">
                {ticketsState.status === "success" ? ticketsState.data.total : "—"}
              </span>
              <span className="font-medium text-muted">tickets</span>
            </span>
            <Can permission="support.desk.ticket.write" scope="ORG">
              <Button leadingIcon={<PlusIcon className="size-4" />} onClick={() => setCreating(true)}>
                New ticket
              </Button>
            </Can>
          </div>
        }
      />

      {/* Controls */}
      <Card className="flex flex-col gap-3 p-4 sm:p-5 lg:flex-row lg:items-center">
        <SearchInput
          label="Search tickets"
          placeholder="Search subject, ID, requester or company…"
          value={search}
          onDebouncedChange={setSearch}
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
          <ErrorState
            message={ticketsState.error.message}
            action={<Button onClick={ticketsState.reload}>Try again</Button>}
          />
        ) : null}
        {ticketsState.status === "success" ? (
          <>
            <DataTable
              columns={columns}
              rows={items}
              rowKey={(t) => t.id}
              caption="Support ticket queue"
              onRowClick={(t) => navigate(`/queue/${t.id}`)}
              mobileCard="auto"
              selection={selection}
              bulkActions={
                <Button
                  variant="secondary"
                  size="sm"
                  leadingIcon={<DownloadIcon className="size-4" />}
                  onClick={exportSelected}
                >
                  Export selected
                </Button>
              }
              emptyState={
                <EmptyState
                  icon={<InboxIcon className="size-6" />}
                  title={hasFilter ? "No tickets match" : "No tickets yet"}
                  description={
                    hasFilter
                      ? "Nothing matches this search and filter set. Clear a filter to widen the queue."
                      : "Nothing has come into the queue yet."
                  }
                  action={
                    hasFilter ? (
                      <Button
                        variant="outline"
                        onClick={() => {
                          setSearch("");
                          setStatus("all");
                          setPriority("all");
                          setAssignee("all");
                        }}
                      >
                        Clear filters
                      </Button>
                    ) : canWrite ? (
                      <Button onClick={() => setCreating(true)}>New ticket</Button>
                    ) : null
                  }
                />
              }
            />
            {ticketsState.data.items.length > 0 ? (
              <Pagination
                page={ticketsState.data.page}
                pages={ticketsState.data.pages}
                total={ticketsState.data.total}
                size={ticketsState.data.size}
                noun="tickets"
                onPageChange={setPage}
                onSizeChange={(n) => {
                  setSize(n);
                  setPage(1);
                }}
              />
            ) : null}
          </>
        ) : null}
      </Card>

      {creating ? (
        <NewTicketDialog
          onClose={() => setCreating(false)}
          onCreated={(created) => navigate(`/queue/${created.id}`)}
        />
      ) : null}
    </div>
  );
}
