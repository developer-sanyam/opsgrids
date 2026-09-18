import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { listProjects } from "@/api/client";
import type { Project, ProjectStatus } from "@/api/types";
import { personName } from "@/data/people";
import { ChevronDown, CloseIcon, LayersIcon, cn } from "@opsgrids/ui";
import { formatDate, formatMoney } from "@/lib/format";
import { useAsync } from "@opsgrids/client";
import {
  Avatar,
  Card,
  DataTable,
  EmptyState,
  ErrorState,
  PageHeader,
  Pill,
  ProjectStatusPill,
  SearchInput,
  Skeleton,
} from "@/ui";
import type { Column } from "@/ui";

type StatusFilter = ProjectStatus | "all";
type SortKey = "name" | "client" | "progress" | "budget" | "spent";

const STATUS_FILTERS: { value: StatusFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "on_track", label: "On track" },
  { value: "at_risk", label: "At risk" },
  { value: "blocked", label: "Blocked" },
  { value: "done", label: "Delivered" },
];

const NO_PROJECTS: Project[] = [];

/** A slim progress meter with its percentage, tabular so columns line up. */
function ProgressMeter({ pct }: { pct: number }) {
  return (
    <div className="flex items-center gap-2 min-w-[7rem]">
      <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-ground">
        <div className="h-full rounded-full bg-accent" style={{ width: `${pct}%` }} />
      </div>
      <span className="w-9 text-right tnum text-xs font-semibold text-ink-soft">{pct}%</span>
    </div>
  );
}

/** A sortable header cell — a button that toggles the active sort key and direction. */
function SortHeader({
  label,
  active,
  dir,
  onClick,
  align = "left",
}: {
  label: string;
  active: boolean;
  dir: "asc" | "desc";
  onClick: () => void;
  align?: "left" | "right";
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "inline-flex items-center gap-1 text-xs font-semibold uppercase tracking-wide transition-colors hover:text-ink",
        active ? "text-accent" : "text-faint",
        align === "right" && "flex-row-reverse",
      )}
    >
      {label}
      <ChevronDown
        className={cn(
          "size-3.5 transition-transform",
          active && dir === "asc" && "rotate-180",
          !active && "opacity-0 group-hover:opacity-100",
        )}
      />
    </button>
  );
}

/** The drill-down: a right-hand drawer with the full picture for one project. */
function ProjectDrawer({ project, onClose }: { project: Project; onClose: () => void }) {
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const overBudget = project.spent_minor > project.budget_minor;
  const remaining = project.budget_minor - project.spent_minor;
  const spentPct = project.budget_minor === 0 ? 0 : Math.round((project.spent_minor / project.budget_minor) * 100);

  return (
    <div className="fixed inset-0 z-50" role="dialog" aria-modal="true" aria-label={`${project.name} details`}>
      <div className="absolute inset-0 bg-ink/40" onClick={onClose} aria-hidden />
      <div className="absolute right-0 top-0 flex h-full w-full max-w-[26rem] flex-col overflow-y-auto bg-surface shadow-float">
        <header className="flex items-start justify-between gap-3 border-b border-line p-6">
          <div className="flex flex-col gap-2">
            <span className="text-xs font-semibold uppercase tracking-wide text-faint tnum">{project.code}</span>
            <h2 className="display text-xl font-extrabold leading-tight">{project.name}</h2>
            <ProjectStatusPill status={project.status} />
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close details"
            className="grid size-9 shrink-0 place-items-center rounded-full text-muted hover:bg-ground hover:text-ink transition-colors"
          >
            <CloseIcon className="size-5" />
          </button>
        </header>

        <div className="flex flex-col gap-6 p-6">
          <div className="flex flex-col gap-2">
            <div className="flex items-center justify-between text-sm">
              <span className="font-semibold text-ink">Progress</span>
              <span className="tnum font-semibold text-ink-soft">{project.progress_pct}%</span>
            </div>
            <div className="h-2 w-full overflow-hidden rounded-full bg-ground">
              <div className="h-full rounded-full bg-accent" style={{ width: `${project.progress_pct}%` }} />
            </div>
          </div>

          <div className="flex flex-col gap-3 rounded-card bg-ground p-4">
            <div className="flex items-center justify-between">
              <span className="text-sm text-muted">Spent</span>
              <span className={cn("tnum font-bold", overBudget ? "text-danger" : "text-ink")}>
                {formatMoney(project.spent_minor, project.currency)}
              </span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-sm text-muted">Budget</span>
              <span className="tnum font-semibold text-ink-soft">
                {formatMoney(project.budget_minor, project.currency)}
              </span>
            </div>
            <div className="h-2 w-full overflow-hidden rounded-full bg-surface">
              <div
                className={cn("h-full rounded-full", overBudget ? "bg-danger" : "bg-accent")}
                style={{ width: `${Math.min(spentPct, 100)}%` }}
              />
            </div>
            <div className="flex items-center justify-between border-t border-line pt-3 text-sm">
              <span className="text-muted">{overBudget ? "Over budget by" : "Remaining"}</span>
              <span className={cn("tnum font-bold", overBudget ? "text-danger" : "text-present")}>
                {formatMoney(Math.abs(remaining), project.currency)}
              </span>
            </div>
          </div>

          <dl className="grid grid-cols-2 gap-x-4 gap-y-4 text-sm">
            <div className="flex flex-col gap-1">
              <dt className="text-xs font-medium text-muted">Client</dt>
              <dd className="font-semibold text-ink">{project.client}</dd>
            </div>
            <div className="flex flex-col gap-1">
              <dt className="text-xs font-medium text-muted">Owner</dt>
              <dd className="flex items-center gap-2 font-semibold text-ink">
                <Avatar name={personName(project.owner_id)} size="xs" />
                {personName(project.owner_id)}
              </dd>
            </div>
            <div className="flex flex-col gap-1">
              <dt className="text-xs font-medium text-muted">Started</dt>
              <dd className="tnum font-semibold text-ink">{formatDate(project.start_date)}</dd>
            </div>
            <div className="flex flex-col gap-1">
              <dt className="text-xs font-medium text-muted">Due</dt>
              <dd className="tnum font-semibold text-ink">{formatDate(project.due_date)}</dd>
            </div>
            <div className="flex flex-col gap-1">
              <dt className="text-xs font-medium text-muted">Open tasks</dt>
              <dd className="tnum font-semibold text-ink">{project.open_tasks}</dd>
            </div>
            <div className="flex flex-col gap-1">
              <dt className="text-xs font-medium text-muted">Billing</dt>
              <dd className="font-semibold text-ink">{project.billable ? "Billable" : "Internal"}</dd>
            </div>
          </dl>
        </div>
      </div>
    </div>
  );
}

export function ProjectsPage() {
  const [searchParams] = useSearchParams();
  const [search, setSearch] = useState(() => searchParams.get("q") ?? "");
  const [status, setStatus] = useState<StatusFilter>("all");
  const [sortKey, setSortKey] = useState<SortKey>("name");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");
  const [selected, setSelected] = useState<Project | null>(null);

  useEffect(() => {
    const q = searchParams.get("q");
    if (q !== null) setSearch(q);
  }, [searchParams]);

  const loader = useCallback(() => listProjects({ search, status }), [search, status]);
  const projects = useAsync(loader, [search, status]);

  const items = useMemo(
    () => (projects.status === "success" ? projects.data : NO_PROJECTS),
    [projects],
  );

  function toggleSort(key: SortKey) {
    if (key === sortKey) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir(key === "progress" || key === "budget" || key === "spent" ? "desc" : "asc");
    }
  }

  const sorted = useMemo(() => {
    const rows = [...items];
    rows.sort((a, b) => {
      let cmp = 0;
      switch (sortKey) {
        case "name":
          cmp = a.name.localeCompare(b.name);
          break;
        case "client":
          cmp = a.client.localeCompare(b.client);
          break;
        case "progress":
          cmp = a.progress_pct - b.progress_pct;
          break;
        case "budget":
          cmp = a.budget_minor - b.budget_minor;
          break;
        case "spent":
          cmp = a.spent_minor - b.spent_minor;
          break;
      }
      return sortDir === "asc" ? cmp : -cmp;
    });
    return rows;
  }, [items, sortKey, sortDir]);

  const columns: Column<Project>[] = [
    {
      key: "name",
      header: <SortHeader label="Project" active={sortKey === "name"} dir={sortDir} onClick={() => toggleSort("name")} />,
      render: (p) => (
        <div className="flex flex-col leading-tight">
          <span className="font-semibold text-ink">{p.name}</span>
          <span className="text-xs text-faint tnum">{p.code}</span>
        </div>
      ),
    },
    {
      key: "client",
      header: <SortHeader label="Client" active={sortKey === "client"} dir={sortDir} onClick={() => toggleSort("client")} />,
      cellClassName: "hidden md:table-cell",
      headerClassName: "hidden md:table-cell",
      render: (p) => <span className="text-ink-soft">{p.client}</span>,
    },
    {
      key: "status",
      header: "Status",
      render: (p) => <ProjectStatusPill status={p.status} />,
    },
    {
      key: "progress",
      header: <SortHeader label="Progress" active={sortKey === "progress"} dir={sortDir} onClick={() => toggleSort("progress")} />,
      cellClassName: "hidden lg:table-cell",
      headerClassName: "hidden lg:table-cell",
      render: (p) => <ProgressMeter pct={p.progress_pct} />,
    },
    {
      key: "budget",
      header: <SortHeader label="Budget / Spent" active={sortKey === "spent"} dir={sortDir} onClick={() => toggleSort("spent")} align="right" />,
      align: "right",
      render: (p) => {
        const over = p.spent_minor > p.budget_minor;
        return (
          <div className="flex flex-col items-end leading-tight">
            <span className={cn("tnum font-semibold", over ? "text-danger" : "text-ink")}>
              {formatMoney(p.spent_minor, p.currency)}
            </span>
            <span className="tnum text-xs text-muted">
              of {formatMoney(p.budget_minor, p.currency)}
            </span>
          </div>
        );
      },
    },
    {
      key: "owner",
      header: "Owner",
      cellClassName: "hidden sm:table-cell",
      headerClassName: "hidden sm:table-cell",
      render: (p) => (
        <span className="inline-flex items-center gap-2 text-ink-soft">
          <Avatar name={personName(p.owner_id)} size="xs" />
          <span className="hidden xl:inline">{personName(p.owner_id)}</span>
        </span>
      ),
    },
  ];

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Portfolio"
        title="Projects"
        actions={
          <span className="rounded-full bg-surface shadow-card px-4 h-11 inline-flex items-center gap-2 text-sm font-semibold">
            <LayersIcon className="size-4 text-muted" />
            <span className="tnum">{items.length}</span>
            <span className="text-muted font-medium">projects</span>
          </span>
        }
      />

      <Card className="p-4 sm:p-5 flex flex-col gap-4">
        <div className="flex flex-col lg:flex-row lg:items-center gap-3">
          <SearchInput
            label="Search projects"
            placeholder="Search by name, client or code…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            containerClassName="lg:max-w-xs"
          />
          <div className="flex items-center gap-1.5 overflow-x-auto -mx-1 px-1">
            {STATUS_FILTERS.map((f) => (
              <Pill key={f.value} active={status === f.value} onClick={() => setStatus(f.value)}>
                {f.label}
              </Pill>
            ))}
          </div>
        </div>
      </Card>

      <Card className="p-2 sm:p-4">
        {projects.status === "loading" ? (
          <div className="flex flex-col gap-3 p-3">
            {Array.from({ length: 8 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full rounded-tile" />
            ))}
          </div>
        ) : null}
        {projects.status === "error" ? <ErrorState message={projects.error.message} /> : null}
        {projects.status === "success" ? (
          <DataTable
            caption="Projects with status, progress and budget"
            columns={columns}
            rows={sorted}
            rowKey={(p) => p.id}
            onRowClick={(p) => setSelected(p)}
            emptyState={
              <EmptyState
                icon={<LayersIcon className="size-6" />}
                title="No matching projects"
                description="No project matches this search or filter. Clear it to see the whole portfolio."
              />
            }
          />
        ) : null}
      </Card>

      {selected ? <ProjectDrawer project={selected} onClose={() => setSelected(null)} /> : null}
    </div>
  );
}
