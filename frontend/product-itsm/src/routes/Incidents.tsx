import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { listIncidents } from "@/api/client";
import type { IncidentSort } from "@/api/client";
import type { Incident, IncidentStatus, Priority } from "@/api/types";
import { formatSlaClock, incidentCategoryLabel, relativeTime } from "@/lib/format";
import { useAsync } from "@opsgrids/client";
import {
  Avatar,
  Card,
  DataTable,
  EmptyState,
  ErrorState,
  IncidentStatusPill,
  PageHeader,
  Pill,
  PriorityPill,
  SearchInput,
  Skeleton,
  SlaPill,
} from "@/ui";
import type { Column } from "@/ui";
import { AlertTriangleIcon, SortIcon } from "@opsgrids/ui";

type PriorityFilter = Priority | "all";
type StatusFilter = IncidentStatus | "all";

const PRIORITY_FILTERS: { value: PriorityFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "P1", label: "P1" },
  { value: "P2", label: "P2" },
  { value: "P3", label: "P3" },
  { value: "P4", label: "P4" },
];

const STATUS_FILTERS: { value: StatusFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "new", label: "New" },
  { value: "in_progress", label: "In progress" },
  { value: "on_hold", label: "On hold" },
  { value: "resolved", label: "Resolved" },
  { value: "closed", label: "Closed" },
];

const SORTS: { value: IncidentSort; label: string }[] = [
  { value: "updated", label: "Last updated" },
  { value: "priority", label: "Priority" },
  { value: "created", label: "Newest" },
];

const NO_INCIDENTS: Incident[] = [];

function isPriority(value: string | null): value is Priority {
  return value === "P1" || value === "P2" || value === "P3" || value === "P4";
}

export function IncidentsPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();

  const [search, setSearch] = useState(() => searchParams.get("q") ?? "");
  const [priority, setPriority] = useState<PriorityFilter>(() => {
    const p = searchParams.get("priority");
    return isPriority(p) ? p : "all";
  });
  const [status, setStatus] = useState<StatusFilter>("all");
  const [sort, setSort] = useState<IncidentSort>("updated");

  // Seed search / priority from the URL when arriving from the sidebar or an Overview link.
  useEffect(() => {
    const q = searchParams.get("q");
    if (q !== null) setSearch(q);
    const p = searchParams.get("priority");
    if (isPriority(p)) setPriority(p);
  }, [searchParams]);

  const loader = useCallback(
    () => listIncidents({ search, priority, status, sort }),
    [search, priority, status, sort],
  );
  const state = useAsync(loader, [search, priority, status, sort]);

  const items = useMemo(
    () => (state.status === "success" ? state.data.items : NO_INCIDENTS),
    [state],
  );

  const columns: Column<Incident>[] = useMemo(
    () => [
      {
        key: "id",
        header: "ID",
        render: (i) => <span className="tnum font-semibold text-ink-soft">{i.id}</span>,
      },
      {
        key: "summary",
        header: "Summary",
        render: (i) => (
          <div className="flex min-w-0 max-w-[22rem] flex-col">
            <span className="truncate font-semibold text-ink">{i.summary}</span>
            <span className="truncate text-xs text-muted">
              {incidentCategoryLabel(i.category)}
              {i.affected_asset_tag ? (
                <>
                  {" · "}
                  <span className="tnum">{i.affected_asset_tag}</span>
                </>
              ) : null}
            </span>
          </div>
        ),
      },
      {
        key: "priority",
        header: "Priority",
        render: (i) => <PriorityPill priority={i.priority} />,
      },
      {
        key: "status",
        header: "Status",
        render: (i) => <IncidentStatusPill status={i.status} />,
      },
      {
        key: "assignee",
        header: "Assignee",
        cellClassName: "hidden md:table-cell",
        headerClassName: "hidden md:table-cell",
        render: (i) => (
          <span className="inline-flex items-center gap-2">
            <Avatar name={i.assignee} size="sm" />
            <span className="text-sm text-ink-soft whitespace-nowrap">{i.assignee}</span>
          </span>
        ),
      },
      {
        key: "sla",
        header: "SLA",
        render: (i) => (
          <div className="flex flex-col gap-1">
            <SlaPill state={i.sla_state} />
            {i.sla_minutes_remaining !== null ? (
              <span className="tnum text-xs text-muted">
                {formatSlaClock(i.sla_minutes_remaining)}
              </span>
            ) : null}
          </div>
        ),
      },
      {
        key: "updated",
        header: "Updated",
        align: "right",
        cellClassName: "hidden sm:table-cell",
        headerClassName: "hidden sm:table-cell",
        render: (i) => (
          <span className="tnum text-sm text-muted whitespace-nowrap">
            {relativeTime(i.updated_at)}
          </span>
        ),
      },
    ],
    [],
  );

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Service desk"
        title="Incidents"
        actions={
          <span className="rounded-full bg-surface shadow-card px-4 h-11 inline-flex items-center gap-2 text-sm font-semibold">
            <AlertTriangleIcon className="size-4 text-muted" />
            <span className="tnum">{items.length}</span>
            <span className="text-muted font-medium">shown</span>
          </span>
        }
      />

      {/* Controls */}
      <Card className="p-4 sm:p-5 flex flex-col gap-4">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
          <SearchInput
            label="Search incidents"
            placeholder="Search by ID, summary, assignee or asset…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            containerClassName="lg:max-w-sm"
          />
          <div className="flex items-center gap-1.5 overflow-x-auto -mx-1 px-1">
            <span className="pr-1 text-xs font-semibold uppercase tracking-wide text-faint shrink-0">
              Priority
            </span>
            {PRIORITY_FILTERS.map((f) => (
              <Pill
                key={f.value}
                active={priority === f.value}
                onClick={() => setPriority(f.value)}
              >
                {f.label}
              </Pill>
            ))}
          </div>
          <label className="ml-auto inline-flex h-11 shrink-0 items-center gap-2 rounded-full bg-ground px-3 text-sm font-semibold text-ink-soft">
            <SortIcon className="size-4 text-muted" />
            <span className="sr-only">Sort incidents</span>
            <select
              value={sort}
              onChange={(e) => setSort(e.target.value as IncidentSort)}
              className="h-full cursor-pointer bg-transparent pr-1 text-sm font-semibold text-ink focus:outline-none"
            >
              {SORTS.map((s) => (
                <option key={s.value} value={s.value}>
                  {s.label}
                </option>
              ))}
            </select>
          </label>
        </div>

        <div className="flex items-center gap-1.5 overflow-x-auto -mx-1 px-1 border-t border-line pt-4">
          <span className="pr-1 text-xs font-semibold uppercase tracking-wide text-faint shrink-0">
            Status
          </span>
          {STATUS_FILTERS.map((f) => (
            <Pill key={f.value} active={status === f.value} onClick={() => setStatus(f.value)}>
              {f.label}
            </Pill>
          ))}
        </div>
      </Card>

      {/* Table */}
      <Card className="p-2 sm:p-4">
        {state.status === "loading" ? (
          <div className="flex flex-col gap-3 p-3">
            {Array.from({ length: 8 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full rounded-tile" />
            ))}
          </div>
        ) : null}
        {state.status === "error" ? <ErrorState message={state.error.message} /> : null}
        {state.status === "success" ? (
          <DataTable
            columns={columns}
            rows={items}
            rowKey={(i) => i.id}
            caption="Incidents"
            onRowClick={(i) => navigate(`/incidents/${i.id}`)}
            emptyState={
              <EmptyState
                icon={<AlertTriangleIcon className="size-6" />}
                title="No matching incidents"
                description="Nothing matches these filters. Try a different priority, status, or clear the search."
              />
            }
          />
        ) : null}
      </Card>
    </div>
  );
}
