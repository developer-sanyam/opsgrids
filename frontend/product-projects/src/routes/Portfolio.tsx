import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { listMilestones, listProjects } from "@/api/client";
import { NOW } from "@/api/fixtures";
import type {
  MilestoneOut,
  ProjectHealth,
  ProjectOut,
  ProjectPhase,
} from "@/api/types";
import { HEALTH_LABEL, PHASE_LABEL } from "@/data/labels";
import { formatDate, formatMoney, relativeDue } from "@/lib/format";
import { useAsync } from "@opsgrids/client";
import {
  Avatar,
  Card,
  DataTable,
  EmptyState,
  ErrorState,
  HealthPill,
  MilestoneStatusPill,
  PageHeader,
  PhasePill,
  Pill,
  ProgressBar,
  SearchInput,
  Skeleton,
} from "@/ui";
import type { Column } from "@/ui";
import { BriefcaseIcon } from "@opsgrids/ui";

const NO_PROJECTS: ProjectOut[] = [];
const NO_MILESTONES: MilestoneOut[] = [];

type HealthFilter = ProjectHealth | "all";
type SortKey = "target" | "name" | "progress" | "health";

const HEALTH_FILTERS: { value: HealthFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "on_track", label: HEALTH_LABEL.on_track },
  { value: "at_risk", label: HEALTH_LABEL.at_risk },
  { value: "delayed", label: HEALTH_LABEL.delayed },
];

const PHASES: ProjectPhase[] = ["discovery", "design", "build", "stabilize", "launch", "live"];

const HEALTH_RANK: Record<ProjectHealth, number> = { delayed: 0, at_risk: 1, on_track: 2 };

function sortProjects(rows: ProjectOut[], key: SortKey): ProjectOut[] {
  const copy = [...rows];
  switch (key) {
    case "name":
      return copy.sort((a, b) => a.name.localeCompare(b.name));
    case "progress":
      return copy.sort((a, b) => b.progress_pct - a.progress_pct);
    case "health":
      return copy.sort((a, b) => HEALTH_RANK[a.health] - HEALTH_RANK[b.health]);
    case "target":
    default:
      return copy.sort((a, b) => a.target_date.localeCompare(b.target_date));
  }
}

/** The drill-down detail for the selected project: money, dates, team and its milestones. */
function ProjectDetail({
  project,
  milestones,
}: {
  project: ProjectOut;
  milestones: MilestoneOut[];
}) {
  const spentPct =
    project.budget_minor === 0
      ? 0
      : Math.round((project.spent_minor / project.budget_minor) * 100);
  const own = milestones
    .filter((m) => m.project_code === project.code)
    .sort((a, b) => a.due_date.localeCompare(b.due_date));

  return (
    <div className="flex flex-col gap-5">
      <div className="flex items-start gap-3">
        <Avatar name={project.owner.name} size="lg" />
        <div className="flex min-w-0 flex-col leading-tight">
          <span className="text-xs font-semibold text-faint tnum">{project.code}</span>
          <span className="text-lg font-extrabold text-ink">{project.name}</span>
          <span className="text-sm text-muted">{project.client}</span>
        </div>
      </div>

      <p className="text-sm leading-relaxed text-ink-soft">{project.summary}</p>

      <div className="flex flex-wrap items-center gap-2">
        <HealthPill health={project.health} />
        <PhasePill phase={project.phase} />
      </div>

      <div className="flex flex-col gap-2">
        <div className="flex items-center justify-between text-sm">
          <span className="text-muted">Delivery progress</span>
          <span className="font-semibold text-ink tnum">{project.progress_pct}%</span>
        </div>
        <ProgressBar value={project.progress_pct} health={project.health} />
      </div>

      <dl className="grid grid-cols-2 gap-3 text-sm">
        <div className="rounded-tile bg-ground px-3 py-2.5">
          <dt className="text-xs text-muted">Owner</dt>
          <dd className="font-semibold text-ink">{project.owner.name}</dd>
        </div>
        <div className="rounded-tile bg-ground px-3 py-2.5">
          <dt className="text-xs text-muted">Team size</dt>
          <dd className="font-semibold text-ink tnum">{project.team_size} people</dd>
        </div>
        <div className="rounded-tile bg-ground px-3 py-2.5">
          <dt className="text-xs text-muted">Start</dt>
          <dd className="font-semibold text-ink tnum">{formatDate(project.start_date)}</dd>
        </div>
        <div className="rounded-tile bg-ground px-3 py-2.5">
          <dt className="text-xs text-muted">Target</dt>
          <dd className="font-semibold text-ink tnum">{formatDate(project.target_date)}</dd>
        </div>
        <div className="rounded-tile bg-ground px-3 py-2.5">
          <dt className="text-xs text-muted">Budget</dt>
          <dd className="font-semibold text-ink tnum">{formatMoney(project.budget_minor)}</dd>
        </div>
        <div className="rounded-tile bg-ground px-3 py-2.5">
          <dt className="text-xs text-muted">Spent</dt>
          <dd className="font-semibold text-ink tnum">
            {formatMoney(project.spent_minor)} · {spentPct}%
          </dd>
        </div>
      </dl>

      <div className="flex flex-col gap-2">
        <h3 className="text-sm font-bold text-ink">Milestones</h3>
        {own.length === 0 ? (
          <p className="text-sm text-muted">No milestones logged for this project yet.</p>
        ) : (
          <ul className="flex flex-col gap-1.5">
            {own.map((m) => (
              <li
                key={m.id}
                className="flex items-center gap-2 rounded-tile bg-ground px-3 py-2"
              >
                <div className="flex min-w-0 flex-col leading-tight">
                  <span className="truncate text-sm font-semibold text-ink">{m.title}</span>
                  <span className="text-xs text-muted tnum">
                    {formatDate(m.due_date)} · {relativeDue(m.due_date, NOW)}
                  </span>
                </div>
                <span className="ml-auto shrink-0">
                  <MilestoneStatusPill status={m.status} />
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

export function PortfolioPage() {
  const [searchParams] = useSearchParams();
  const [search, setSearch] = useState(() => searchParams.get("q") ?? "");
  const [health, setHealth] = useState<HealthFilter>("all");
  const [phase, setPhase] = useState<ProjectPhase | "all">("all");
  const [sort, setSort] = useState<SortKey>("target");
  const [selectedId, setSelectedId] = useState<string | null>(null);

  useEffect(() => {
    const q = searchParams.get("q");
    if (q !== null) setSearch(q);
  }, [searchParams]);

  const projectsState = useAsync(
    () => listProjects({ search, health, phase }),
    [search, health, phase],
  );
  const milestonesState = useAsync(listMilestones, []);

  const items = useMemo(
    () => (projectsState.status === "success" ? projectsState.data.items : NO_PROJECTS),
    [projectsState],
  );
  const milestones = useMemo(
    () => (milestonesState.status === "success" ? milestonesState.data.items : NO_MILESTONES),
    [milestonesState],
  );

  const rows = useMemo(() => sortProjects(items, sort), [items, sort]);

  // Keep a valid selection: default to the first row, and recover if the current one filters out.
  useEffect(() => {
    if (rows.length === 0) {
      setSelectedId(null);
      return;
    }
    setSelectedId((prev) =>
      prev && rows.some((r) => r.id === prev) ? prev : (rows[0]?.id ?? null),
    );
  }, [rows]);

  const selected = rows.find((r) => r.id === selectedId) ?? null;

  const columns: Column<ProjectOut>[] = [
    {
      key: "project",
      header: "Project",
      render: (p) => (
        <div className="flex min-w-0 flex-col leading-tight">
          <span className="truncate font-semibold text-ink">{p.name}</span>
          <span className="truncate text-xs text-muted tnum">
            {p.code} · {p.client}
          </span>
        </div>
      ),
    },
    {
      key: "owner",
      header: "Owner",
      cellClassName: "hidden md:table-cell",
      headerClassName: "hidden md:table-cell",
      render: (p) => (
        <div className="flex items-center gap-2">
          <Avatar name={p.owner.name} size="xs" />
          <span className="truncate text-sm text-ink-soft">{p.owner.name}</span>
        </div>
      ),
    },
    {
      key: "phase",
      header: "Phase",
      cellClassName: "hidden lg:table-cell",
      headerClassName: "hidden lg:table-cell",
      render: (p) => <PhasePill phase={p.phase} />,
    },
    {
      key: "progress",
      header: "Progress",
      cellClassName: "hidden sm:table-cell w-40",
      headerClassName: "hidden sm:table-cell",
      render: (p) => <ProgressBar value={p.progress_pct} health={p.health} showLabel />,
    },
    {
      key: "target",
      header: "Target",
      cellClassName: "hidden xl:table-cell whitespace-nowrap",
      headerClassName: "hidden xl:table-cell",
      render: (p) => (
        <span className="text-sm text-ink-soft tnum">{formatDate(p.target_date)}</span>
      ),
    },
    {
      key: "health",
      header: "Health",
      align: "right",
      render: (p) => (
        <span className="inline-flex justify-end">
          <HealthPill health={p.health} />
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
            <BriefcaseIcon className="size-4 text-muted" />
            <span className="tnum">{items.length}</span>
            <span className="text-muted font-medium">projects</span>
          </span>
        }
      />

      {/* Controls */}
      <Card className="p-4 sm:p-5 flex flex-col gap-4">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
          <SearchInput
            label="Search projects"
            placeholder="Search by name, code, client or owner…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            containerClassName="lg:max-w-sm"
          />
          <div className="flex items-center gap-1.5 overflow-x-auto -mx-1 px-1">
            {HEALTH_FILTERS.map((f) => (
              <Pill key={f.value} active={health === f.value} onClick={() => setHealth(f.value)}>
                {f.label}
              </Pill>
            ))}
          </div>
          <label className="ml-auto inline-flex h-11 shrink-0 items-center gap-2 rounded-full bg-ground px-4 text-sm font-semibold text-ink-soft">
            <span className="text-muted">Sort</span>
            <select
              value={sort}
              onChange={(e) => setSort(e.target.value as SortKey)}
              className="cursor-pointer bg-transparent font-semibold text-ink focus:outline-none"
            >
              <option value="target">Target date</option>
              <option value="name">Name A–Z</option>
              <option value="progress">Progress</option>
              <option value="health">Health</option>
            </select>
          </label>
        </div>

        <div className="flex items-center gap-1.5 overflow-x-auto -mx-1 px-1 border-t border-line pt-4">
          <span className="pr-1 text-xs font-semibold uppercase tracking-wide text-faint shrink-0">
            Phase
          </span>
          <Pill active={phase === "all"} onClick={() => setPhase("all")}>
            All
          </Pill>
          {PHASES.map((ph) => (
            <Pill key={ph} active={phase === ph} onClick={() => setPhase(ph)}>
              {PHASE_LABEL[ph]}
            </Pill>
          ))}
        </div>
      </Card>

      {/* Table + drill-down */}
      <div className="grid grid-cols-1 gap-5 xl:grid-cols-3 items-start">
        <Card className="p-2 sm:p-4 xl:col-span-2">
          {projectsState.status === "loading" ? (
            <div className="flex flex-col gap-3 p-3">
              {Array.from({ length: 8 }).map((_, i) => (
                <Skeleton key={i} className="h-12 w-full rounded-tile" />
              ))}
            </div>
          ) : projectsState.status === "error" ? (
            <ErrorState message={projectsState.error.message} />
          ) : (
            <DataTable
              columns={columns}
              rows={rows}
              rowKey={(p) => p.id}
              caption="Project portfolio"
              onRowClick={(p) => setSelectedId(p.id)}
              isSelected={(p) => p.id === selectedId}
              emptyState={
                <EmptyState
                  icon={<BriefcaseIcon className="size-6" />}
                  title="No matching projects"
                  description="No project matches these filters. Clear the search or switch back to all health and phases."
                />
              }
            />
          )}
        </Card>

        <Card className="p-6 xl:col-span-1 xl:sticky xl:top-4">
          {projectsState.status === "loading" ? (
            <Skeleton className="h-96 w-full rounded-tile" />
          ) : selected ? (
            <ProjectDetail project={selected} milestones={milestones} />
          ) : (
            <EmptyState
              icon={<BriefcaseIcon className="size-6" />}
              title="Select a project"
              description="Pick a row to see its owner, budget, dates and milestones here."
            />
          )}
        </Card>
      </div>
    </div>
  );
}
