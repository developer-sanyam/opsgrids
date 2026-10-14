import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { DEFAULT_PAGE_SIZE, deleteProject, getAllMembers, listProjects } from "@/api/client";
import type { ProjectHealth, ProjectOut, ProjectPhase } from "@/api/types";
import { portfolioEntity } from "@/data/importExport/portfolio";
import type { PortfolioFilters } from "@/data/importExport/portfolio";
import { HEALTH_LABEL, PHASE_LABEL } from "@/data/labels";
import { downloadCsv } from "@/lib/csv";
import { formatDate } from "@/lib/format";
import { Can, problemMessage, useAsync, useCan, useImportExport, useMutation } from "@opsgrids/client";
import {
  Avatar,
  Button,
  Card,
  ConfirmDialog,
  DataTable,
  EmptyState,
  ErrorState,
  HealthPill,
  PageHeader,
  Pagination,
  Pill,
  PhasePill,
  ProgressBar,
  RowActions,
  SearchInput,
  Select,
  Skeleton,
} from "@/ui";
import type { Column, SelectOption, TableSelection } from "@/ui";
import {
  BriefcaseIcon,
  DownloadIcon,
  ImportExportButton,
  useDebouncedValue,
  useToast,
} from "@opsgrids/ui";
import { MilestoneFormDialog } from "./MilestoneFormDialog";
import { ProjectDetailDialog } from "./ProjectDetailDialog";
import { ProjectFormDialog } from "./ProjectFormDialog";

type HealthFilter = ProjectHealth | "all";
type SortKey = "code" | "progress" | "target" | "name" | "health";

const HEALTH_FILTERS: { value: HealthFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "on_track", label: HEALTH_LABEL.on_track },
  { value: "at_risk", label: HEALTH_LABEL.at_risk },
  { value: "delayed", label: HEALTH_LABEL.delayed },
];

const PHASES: ProjectPhase[] = ["discovery", "design", "build", "stabilize", "launch", "live"];

const SORT_OPTIONS: SelectOption[] = [
  { value: "code", label: "Code" },
  { value: "name", label: "Name A–Z" },
  { value: "progress", label: "Progress (highest first)" },
  { value: "target", label: "Target date" },
  { value: "health", label: "Health (worst first)" },
];

const WRITE = "projects.portfolio.project.write";

export function PortfolioPage() {
  const toast = useToast();
  const [searchParams, setSearchParams] = useSearchParams();
  const [search, setSearch] = useState(() => searchParams.get("q") ?? "");
  const debouncedSearch = useDebouncedValue(search);
  const [health, setHealth] = useState<HealthFilter>("all");
  const [phase, setPhase] = useState<ProjectPhase | "all">("all");
  const [sort, setSort] = useState<SortKey>("code");
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(DEFAULT_PAGE_SIZE);
  const [selected, setSelected] = useState<ProjectOut | null>(null);
  const [editing, setEditing] = useState<ProjectOut | null>(null);
  const [creating, setCreating] = useState(false);
  const [deleting, setDeleting] = useState<ProjectOut | null>(null);
  const [addingMilestoneTo, setAddingMilestoneTo] = useState<ProjectOut | null>(null);
  const [selectedIds, setSelectedIds] = useState<ReadonlySet<string>>(new Set());

  const canWrite = useCan(WRITE);

  // A query-string search or `?new=1` (the Overview's "New project" button) opens the screen
  // already primed. Only takes effect on arrival — after that the field and the dialog own it.
  useEffect(() => {
    const q = searchParams.get("q");
    if (q !== null && q !== search) setSearch(q);
    if (searchParams.get("new") === "1") {
      setCreating(true);
      const next = new URLSearchParams(searchParams);
      next.delete("new");
      setSearchParams(next, { replace: true });
    }
    // Only ever reads the URL on mount/navigation, never on every keystroke.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams]);

  useEffect(() => {
    setPage(1);
  }, [debouncedSearch, health, phase, sort]);

  // A ticked row is a row on screen; a new page or filter invalidates the selection rather than
  // carrying it to rows nobody saw ticked.
  useEffect(() => {
    setSelectedIds(new Set());
  }, [debouncedSearch, health, phase, sort, page, size]);

  const loader = useCallback(
    () =>
      listProjects({
        q: debouncedSearch,
        health,
        phase,
        sort,
        page,
        size,
      }),
    [debouncedSearch, health, phase, sort, page, size],
  );
  const projects = useAsync(loader, [debouncedSearch, health, phase, sort, page, size]);
  const rows = projects.status === "success" ? projects.data.items : [];

  // What an export of this list must respect: the filters and sort on screen, not the page window.
  const exportFilters = useMemo<PortfolioFilters>(
    () => ({ q: debouncedSearch, health, phase, sort }),
    [debouncedSearch, health, phase, sort],
  );
  // The list's own reload (one identity for the life of the screen), so projects an import created
  // show up without a refresh.
  const dx = useImportExport(portfolioEntity, { filters: exportFilters, onImported: projects.reload });

  const selection: TableSelection<ProjectOut> = {
    selected: selectedIds,
    onToggle: (key, next) =>
      setSelectedIds((prev) => {
        const copy = new Set(prev);
        if (next) copy.add(key);
        else copy.delete(key);
        return copy;
      }),
    onToggleAll: (next) => setSelectedIds(next ? new Set(rows.map((p) => p.id)) : new Set()),
    label: (p) => p.name,
  };

  function exportSelected() {
    const chosen = rows.filter((p) => selectedIds.has(p.id));
    downloadCsv(`projects-${new Date().toISOString().slice(0, 10)}.csv`, chosen, [
      { header: "Project", value: (p) => p.name },
      { header: "Code", value: (p) => p.code },
      { header: "Owner", value: (p) => p.owner.name },
      { header: "Phase", value: (p) => PHASE_LABEL[p.phase] },
      { header: "Progress %", value: (p) => String(p.progress_pct) },
      { header: "Target date", value: (p) => formatDate(p.target_date) },
      { header: "Health", value: (p) => HEALTH_LABEL[p.health] },
    ]);
  }

  // The roster doesn't depend on the filters, so it loads once, separately — the source for the
  // create/edit form's owner picker.
  const membersState = useAsync(useCallback(getAllMembers, []), []);
  const members = useMemo(
    () => (membersState.status === "success" ? membersState.data : []),
    [membersState],
  );

  const remove = useMutation((project: ProjectOut) => deleteProject(project.code), {
    onSuccess: (_result, project) => {
      setDeleting(null);
      setSelected(null);
      toast.success(`${project.name} and its milestones and cards are gone.`);
      projects.reload();
    },
  });

  const columns: Column<ProjectOut>[] = [
    {
      key: "project",
      header: "Project",
      mobile: "title",
      render: (p) => (
        <div className="flex min-w-0 flex-col leading-tight">
          <span className="truncate font-semibold text-ink">{p.name}</span>
          <span className="truncate text-xs text-muted tnum">
            {p.code} · {p.client || "No client"}
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
      render: (p) => <span className="text-sm text-ink-soft tnum">{formatDate(p.target_date)}</span>,
    },
    {
      key: "health",
      header: "Health",
      align: "right",
      mobile: "status",
      render: (p) => (
        <span className="inline-flex justify-end">
          <HealthPill health={p.health} />
        </span>
      ),
    },
    {
      key: "actions",
      header: "",
      align: "right",
      mobile: "actions",
      render: (p) => (
        <RowActions
          label={p.name}
          actions={[
            { label: "View", onSelect: () => setSelected(p) },
            { label: "Edit", onSelect: () => setEditing(p), hidden: !canWrite },
            { label: "Delete", tone: "danger", onSelect: () => setDeleting(p), hidden: !canWrite },
          ]}
        />
      ),
    },
  ];

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Portfolio"
        title="Projects"
        actions={
          <div className="flex flex-wrap items-center gap-2">
            {projects.status === "success" ? (
              <span className="rounded-full bg-surface shadow-card px-4 h-11 inline-flex items-center gap-2 text-sm font-semibold">
                <BriefcaseIcon className="size-4 text-muted" />
                <span className="tnum">{projects.data.total}</span>
                <span className="text-muted font-medium">projects</span>
              </span>
            ) : null}
            <ImportExportButton
              meta={dx.meta}
              state={dx.state}
              actions={dx.actions}
              canImport={canWrite}
              canExport
            />
            <Can permission={WRITE}>
              <Button onClick={() => setCreating(true)}>New project</Button>
            </Can>
          </div>
        }
      />

      {/* Controls */}
      <Card className="p-4 sm:p-5 flex flex-col gap-4">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-end">
          <SearchInput
            label="Search projects"
            placeholder="Search by name, code, client or summary…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            containerClassName="lg:max-w-sm"
          />
          <div className="flex items-center gap-1.5 overflow-x-auto -mx-1 px-1 lg:h-11">
            {HEALTH_FILTERS.map((f) => (
              <Pill key={f.value} active={health === f.value} onClick={() => setHealth(f.value)}>
                {f.label}
              </Pill>
            ))}
          </div>
          <Select
            label="Sort by"
            containerClassName="lg:ml-auto lg:w-56"
            value={sort}
            onChange={(e) => setSort(e.target.value as SortKey)}
            options={SORT_OPTIONS}
          />
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

      <Card className="p-2 sm:p-4">
        {projects.status === "loading" ? (
          <div className="flex flex-col gap-3 p-3">
            {Array.from({ length: 8 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full rounded-tile" />
            ))}
          </div>
        ) : null}
        {projects.status === "error" ? (
          <ErrorState
            title="Couldn't load projects"
            message={problemMessage(projects.error)}
            action={<Button onClick={projects.reload}>Try again</Button>}
          />
        ) : null}
        {projects.status === "success" ? (
          <>
            <DataTable
              caption="Project portfolio"
              columns={columns}
              rows={rows}
              rowKey={(p) => p.id}
              onRowClick={setSelected}
              mobileCard="auto"
              selection={selection}
              bulkActions={
                <Button
                  variant="outline"
                  size="sm"
                  leadingIcon={<DownloadIcon className="size-4" />}
                  onClick={exportSelected}
                >
                  Export selected
                </Button>
              }
              emptyState={
                <EmptyState
                  icon={<BriefcaseIcon className="size-6" />}
                  title={search ? "No projects match that search" : "No projects yet"}
                  description={
                    search
                      ? "Try a shorter search term, or clear it to see the whole portfolio."
                      : "Create the first project to get started."
                  }
                  action={
                    search ? (
                      <Button variant="outline" onClick={() => setSearch("")}>
                        Clear search
                      </Button>
                    ) : (
                      <Can permission={WRITE}>
                        <Button onClick={() => setCreating(true)}>New project</Button>
                      </Can>
                    )
                  }
                />
              }
            />
            {projects.data.items.length > 0 ? (
              <Pagination
                page={page}
                pages={projects.data.pages}
                total={projects.data.total}
                size={size}
                noun="projects"
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

      {selected ? (
        <ProjectDetailDialog
          project={selected}
          onClose={() => setSelected(null)}
          onEdit={() => {
            setEditing(selected);
            setSelected(null);
          }}
          onDelete={() => {
            setDeleting(selected);
            setSelected(null);
          }}
          onAddMilestone={() => {
            setAddingMilestoneTo(selected);
            setSelected(null);
          }}
        />
      ) : null}

      {creating ? (
        <ProjectFormDialog
          project={null}
          members={members}
          onClose={() => setCreating(false)}
          onSaved={(_project, message) => {
            toast.success(message);
            projects.reload();
          }}
        />
      ) : null}
      {editing ? (
        <ProjectFormDialog
          project={editing}
          members={members}
          onClose={() => setEditing(null)}
          onSaved={(_project, message) => {
            setEditing(null);
            toast.success(message);
            projects.reload();
          }}
        />
      ) : null}

      {addingMilestoneTo ? (
        <MilestoneFormDialog
          milestone={null}
          projects={[addingMilestoneTo]}
          members={members}
          defaultProjectCode={addingMilestoneTo.code}
          onClose={() => setAddingMilestoneTo(null)}
          onSaved={(_milestone, message) => {
            setAddingMilestoneTo(null);
            toast.success(message);
            projects.reload();
          }}
        />
      ) : null}

      <ConfirmDialog
        open={deleting !== null}
        onClose={() => {
          setDeleting(null);
          remove.reset();
        }}
        onConfirm={() => deleting && void remove.mutate(deleting)}
        title={deleting ? `Delete ${deleting.name}?` : "Delete this project?"}
        body={`${deleting?.name} and its ${deleting?.milestone_count ?? 0} milestone${deleting?.milestone_count === 1 ? "" : "s"} and every card on its board are removed. This can't be undone.`}
        confirmLabel="Delete"
        pendingLabel="Deleting…"
        pending={remove.pending}
        {...(remove.error ? { error: problemMessage(remove.error) } : {})}
      />
    </div>
  );
}
