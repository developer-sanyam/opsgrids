import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { DEFAULT_PAGE_SIZE, deleteProject, getPeople, listProjects } from "@/api/client";
import type { Person, Project, ProjectStatus } from "@/api/types";
import { useDebouncedValue } from "@/lib/useDebouncedValue";
import { Can, problemMessage, useAsync, useCan, useMutation } from "@opsgrids/client";
import {
  Avatar,
  Button,
  Card,
  ConfirmDialog,
  DataTable,
  EmptyState,
  ErrorState,
  LayersIcon,
  PageHeader,
  Pagination,
  Pill,
  RowActions,
  SearchInput,
  Select,
  Skeleton,
  useToast,
} from "@opsgrids/ui";
import type { Column, SelectOption } from "@opsgrids/ui";
import { formatMoney } from "@/lib/format";
import { ProjectStatusPill } from "@/ui";
import { ProjectDetailDialog } from "./ProjectDetailDialog";
import { ProjectFormDialog } from "./ProjectFormDialog";

type StatusFilter = ProjectStatus | "all";
type BillableFilter = "all" | "billable" | "internal";
type SortKey = "code" | "progress" | "due";

const STATUS_FILTERS: { value: StatusFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "on_track", label: "On track" },
  { value: "at_risk", label: "At risk" },
  { value: "blocked", label: "Blocked" },
  { value: "done", label: "Delivered" },
];

const BILLABLE_OPTIONS: SelectOption[] = [
  { value: "all", label: "Billable & internal" },
  { value: "billable", label: "Billable only" },
  { value: "internal", label: "Internal only" },
];

const SORT_OPTIONS: SelectOption[] = [
  { value: "code", label: "Code" },
  { value: "progress", label: "Progress (highest first)" },
  { value: "due", label: "Due date (soonest first)" },
];

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

export function ProjectsPage() {
  const toast = useToast();
  const [searchParams, setSearchParams] = useSearchParams();
  const [search, setSearch] = useState(() => searchParams.get("q") ?? "");
  const debouncedSearch = useDebouncedValue(search);
  const [status, setStatus] = useState<StatusFilter>("all");
  const [billable, setBillable] = useState<BillableFilter>("all");
  const [sort, setSort] = useState<SortKey>("code");
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(DEFAULT_PAGE_SIZE);
  const [selected, setSelected] = useState<Project | null>(null);
  const [editing, setEditing] = useState<Project | null>(null);
  const [creating, setCreating] = useState(false);
  const [deleting, setDeleting] = useState<Project | null>(null);

  const canWrite = useCan("pulseboard.engagements.project.write");

  // A query-string search opens the list already filtered (e.g. the Overview's "needs
  // attention" links here). Only takes effect once, on arrival — after that the field owns it.
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
  }, [debouncedSearch, status, billable, sort]);

  const loader = useCallback(
    () =>
      listProjects({
        q: debouncedSearch,
        status,
        billable: billable === "all" ? undefined : billable === "billable",
        sort,
        page,
        size,
      }),
    [debouncedSearch, status, billable, sort, page, size],
  );
  const projects = useAsync(loader, [debouncedSearch, status, billable, sort, page, size]);

  // The roster doesn't depend on the filters, so it loads once, separately.
  const peopleState = useAsync(useCallback(getPeople, []), []);
  const personById = useMemo(
    () => new Map<string, Person>((peopleState.status === "success" ? peopleState.data : []).map((p) => [p.id, p])),
    [peopleState],
  );
  const people = peopleState.status === "success" ? peopleState.data : [];
  const ownerName = useCallback(
    (id: string | null): string => {
      if (id === null) return "Unassigned";
      return personById.get(id)?.name ?? "Unknown";
    },
    [personById],
  );

  const remove = useMutation((project: Project) => deleteProject(project.code), {
    onSuccess: (_result, project) => {
      setDeleting(null);
      setSelected(null);
      toast.success(`${project.name} deleted.`);
      projects.reload();
    },
  });

  const columns: Column<Project>[] = [
    {
      key: "name",
      header: "Project",
      render: (p) => (
        <div className="flex flex-col leading-tight">
          <span className="font-semibold text-ink">{p.name}</span>
          <span className="text-xs text-faint tnum">{p.code}</span>
        </div>
      ),
    },
    {
      key: "client",
      header: "Client",
      cellClassName: "hidden md:table-cell",
      headerClassName: "hidden md:table-cell",
      render: (p) => <span className="text-ink-soft">{p.client || "—"}</span>,
    },
    { key: "status", header: "Status", render: (p) => <ProjectStatusPill status={p.status} /> },
    {
      key: "progress",
      header: "Progress",
      cellClassName: "hidden lg:table-cell",
      headerClassName: "hidden lg:table-cell",
      render: (p) => <ProgressMeter pct={p.progress_pct} />,
    },
    {
      key: "budget",
      header: "Budget / Spent",
      align: "right",
      render: (p) => {
        const over = p.spent_minor > p.budget_minor;
        return (
          <div className="flex flex-col items-end leading-tight">
            <span className={over ? "tnum font-semibold text-danger" : "tnum font-semibold text-ink"}>
              {formatMoney(p.spent_minor, p.currency)}
            </span>
            <span className="tnum text-xs text-muted">of {formatMoney(p.budget_minor, p.currency)}</span>
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
          <Avatar name={ownerName(p.owner_id)} size="xs" />
          <span className="hidden xl:inline">{ownerName(p.owner_id)}</span>
        </span>
      ),
    },
    {
      key: "actions",
      header: "",
      align: "right",
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
          <Can permission="pulseboard.engagements.project.write">
            <Button onClick={() => setCreating(true)}>New project</Button>
          </Can>
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
          <div className="flex flex-wrap items-center gap-3 lg:ml-auto">
            <Select
              label="Billing"
              containerClassName="w-48"
              value={billable}
              onChange={(e) => setBillable(e.target.value as BillableFilter)}
              options={BILLABLE_OPTIONS}
            />
            <Select
              label="Sort by"
              containerClassName="w-56"
              value={sort}
              onChange={(e) => setSort(e.target.value as SortKey)}
              options={SORT_OPTIONS}
            />
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
              caption="Projects with status, progress and budget"
              columns={columns}
              rows={projects.data.items}
              rowKey={(p) => p.id}
              onRowClick={setSelected}
              emptyState={
                <EmptyState
                  icon={<LayersIcon className="size-6" />}
                  title={search ? "No projects match that search" : "No projects yet"}
                  description={
                    search
                      ? "Try a shorter search term, or clear it to see the whole portfolio."
                      : "Create the first engagement to get started."
                  }
                  action={
                    search ? (
                      <Button variant="outline" onClick={() => setSearch("")}>
                        Clear search
                      </Button>
                    ) : (
                      <Can permission="pulseboard.engagements.project.write">
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
          ownerName={ownerName(selected.owner_id)}
          onClose={() => setSelected(null)}
          onEdit={() => {
            setEditing(selected);
            setSelected(null);
          }}
          onDelete={() => {
            setDeleting(selected);
            setSelected(null);
          }}
        />
      ) : null}

      {creating ? (
        <ProjectFormDialog
          project={null}
          people={people}
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
          people={people}
          onClose={() => setEditing(null)}
          onSaved={(_project, message) => {
            setEditing(null);
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
        body={`${deleting?.name} and its tasks are removed. This can't be undone.`}
        confirmLabel="Delete"
        pendingLabel="Deleting…"
        pending={remove.pending}
        {...(remove.error ? { error: problemMessage(remove.error) } : {})}
      />
    </div>
  );
}
