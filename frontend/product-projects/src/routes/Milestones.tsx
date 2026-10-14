import { useCallback, useMemo, useState } from "react";

import { deleteMilestone, getAllMembers, getAllMilestones, getAllProjects, updateMilestone } from "@/api/client";
import type { MilestoneOut, MilestoneStatus } from "@/api/types";
import { MILESTONE_STATUS_LABEL } from "@/data/labels";
import { formatDate, relativeDue, today } from "@/lib/format";
import { Can, problemMessage, useAsync, useCan, useMutation } from "@opsgrids/client";
import {
  Avatar,
  Card,
  ConfirmDialog,
  EmptyState,
  ErrorState,
  MilestoneStatusPill,
  PageHeader,
  Pill,
  ProgressBar,
  RowActions,
  Skeleton,
} from "@/ui";
import { Button, FlagIcon, useToast } from "@opsgrids/ui";
import { MilestoneFormDialog } from "./MilestoneFormDialog";

type StatusFilter = MilestoneStatus | "all" | "attention";

const FILTERS: { value: StatusFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "attention", label: "Needs attention" },
  { value: "due_soon", label: MILESTONE_STATUS_LABEL.due_soon },
  { value: "on_track", label: MILESTONE_STATUS_LABEL.on_track },
  { value: "at_risk", label: MILESTONE_STATUS_LABEL.at_risk },
  { value: "slipping", label: MILESTONE_STATUS_LABEL.slipping },
  { value: "completed", label: MILESTONE_STATUS_LABEL.completed },
];

const ATTENTION: ReadonlySet<MilestoneStatus> = new Set<MilestoneStatus>(["slipping", "at_risk"]);
const WRITE = "projects.delivery.milestone.write";

function MilestoneRow({
  milestone,
  canWrite,
  onEdit,
  onComplete,
  onDelete,
}: {
  milestone: MilestoneOut;
  canWrite: boolean;
  onEdit: () => void;
  onComplete: () => void;
  onDelete: () => void;
}) {
  const d = milestone.due_date ? new Date(`${milestone.due_date}T00:00:00Z`) : null;
  const day = d?.toLocaleDateString("en-IN", { day: "2-digit", timeZone: "UTC" });
  const month = d?.toLocaleDateString("en-IN", { month: "short", timeZone: "UTC" });
  const overdue = milestone.status === "slipping";

  return (
    <li className="flex flex-col gap-3 rounded-tile px-3 py-3 hover:bg-ground/60 transition-colors sm:flex-row sm:items-center sm:gap-4">
      <div
        className={
          "grid size-12 shrink-0 place-items-center rounded-tile text-center leading-none " +
          (overdue ? "bg-danger-soft text-danger" : "bg-ground text-ink-soft")
        }
      >
        {d ? (
          <>
            <span className="text-base font-extrabold tnum">{day}</span>
            <span className="text-caption font-bold uppercase tracking-wide">{month}</span>
          </>
        ) : (
          <span className="text-caption font-bold uppercase tracking-wide text-faint">No date</span>
        )}
      </div>

      <div className="flex min-w-0 flex-1 flex-col gap-1.5">
        <div className="flex min-w-0 flex-col leading-tight">
          <span className="truncate text-sm font-bold text-ink">{milestone.title}</span>
          <span className="truncate text-xs text-muted tnum">
            {milestone.project_code} · {milestone.project_name}
          </span>
        </div>
        <ProgressBar
          value={milestone.progress_pct}
          className="max-w-xs"
          {...(milestone.status === "slipping" || milestone.status === "at_risk"
            ? { health: milestone.status === "slipping" ? ("delayed" as const) : ("at_risk" as const) }
            : {})}
        />
      </div>

      <div className="flex shrink-0 items-center gap-2 sm:hidden">
        <Avatar name={milestone.owner.name} size="sm" />
        <span className="text-xs font-semibold text-ink">{milestone.owner.name}</span>
      </div>
      <div className="hidden shrink-0 items-center gap-2 sm:flex">
        <Avatar name={milestone.owner.name} size="sm" />
        <div className="flex flex-col leading-tight">
          <span className="text-xs font-semibold text-ink">{milestone.owner.name}</span>
          <span className="text-caption text-muted">{milestone.owner.role}</span>
        </div>
      </div>

      <div className="flex shrink-0 items-center justify-between gap-2 sm:flex-col sm:items-end">
        <div className="flex flex-col items-start gap-1 sm:items-end">
          <MilestoneStatusPill status={milestone.status} />
          <span className="text-caption font-medium text-faint tnum">
            {formatDate(milestone.due_date)} · {relativeDue(milestone.due_date, today())}
          </span>
        </div>
        {canWrite ? (
          <RowActions
            label={milestone.title}
            actions={[
              { label: "Edit", onSelect: onEdit },
              {
                label: "Mark completed",
                onSelect: onComplete,
                disabled: milestone.status === "completed",
                disabledReason: "Already completed.",
              },
              { label: "Delete", tone: "danger", onSelect: onDelete },
            ]}
          />
        ) : null}
      </div>
    </li>
  );
}

export function MilestonesPage() {
  const toast = useToast();
  const [filter, setFilter] = useState<StatusFilter>("all");
  const [editing, setEditing] = useState<MilestoneOut | null>(null);
  const [creating, setCreating] = useState(false);
  const [deleting, setDeleting] = useState<MilestoneOut | null>(null);

  const canWrite = useCan(WRITE);

  const milestonesState = useAsync(useCallback(() => getAllMilestones(), []), []);
  const projectsState = useAsync(useCallback(getAllProjects, []), []);
  const membersState = useAsync(useCallback(getAllMembers, []), []);

  const items = useMemo(
    () => (milestonesState.status === "success" ? milestonesState.data : []),
    [milestonesState],
  );
  const projects = projectsState.status === "success" ? projectsState.data : [];
  const members = membersState.status === "success" ? membersState.data : [];

  const rows = useMemo(() => {
    const filtered = items.filter((m) => {
      if (filter === "all") return true;
      if (filter === "attention") return ATTENTION.has(m.status);
      return m.status === filter;
    });
    // Unfinished first, soonest due; completed sink to the bottom.
    return [...filtered].sort((a, b) => {
      const aDone = a.status === "completed" ? 1 : 0;
      const bDone = b.status === "completed" ? 1 : 0;
      if (aDone !== bDone) return aDone - bDone;
      if (a.due_date === null && b.due_date === null) return 0;
      if (a.due_date === null) return 1;
      if (b.due_date === null) return -1;
      return a.due_date.localeCompare(b.due_date);
    });
  }, [items, filter]);

  const attentionCount = useMemo(() => items.filter((m) => ATTENTION.has(m.status)).length, [items]);

  const complete = useMutation(
    (milestone: MilestoneOut) => updateMilestone(milestone.id, { status: "completed", progress_pct: 100 }),
    {
      onSuccess: (saved) => {
        toast.success(`${saved.title} marked complete.`);
        milestonesState.reload();
      },
      onError: (error) => toast.error("Couldn't mark this complete", { description: problemMessage(error) }),
    },
  );

  const remove = useMutation((milestone: MilestoneOut) => deleteMilestone(milestone.id), {
    onSuccess: (_result, milestone) => {
      setDeleting(null);
      toast.success(`${milestone.title} deleted.`);
      milestonesState.reload();
      projectsState.reload();
    },
  });

  const loading = milestonesState.status === "loading";
  const failed = milestonesState.status === "error";

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Delivery"
        title="Milestones"
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <span className="rounded-full bg-surface shadow-card px-4 h-11 inline-flex items-center gap-2 text-sm font-semibold">
              <FlagIcon className="size-4 text-muted" />
              <span className="tnum">{attentionCount}</span>
              <span className="text-muted font-medium">need attention</span>
            </span>
            <Can permission={WRITE}>
              <Button onClick={() => setCreating(true)}>New milestone</Button>
            </Can>
          </div>
        }
      />

      <Card className="p-4 sm:p-5 flex flex-col gap-4">
        <div role="group" aria-label="Filter milestones" className="flex items-center gap-1.5 overflow-x-auto -mx-1 -my-1 px-1 py-1">
          {FILTERS.map((f) => (
            <Pill key={f.value} active={filter === f.value} onClick={() => setFilter(f.value)}>
              {f.label}
            </Pill>
          ))}
        </div>

        {loading ? (
          <div className="flex flex-col gap-2 py-1">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-16 w-full rounded-tile" />
            ))}
          </div>
        ) : failed ? (
          <ErrorState
            title="Couldn't load milestones"
            message={problemMessage(milestonesState.error)}
            action={<Button onClick={milestonesState.reload}>Try again</Button>}
          />
        ) : rows.length === 0 ? (
          <EmptyState
            icon={<FlagIcon className="size-6" />}
            title={filter === "all" ? "No milestones yet" : "No milestones here"}
            description={
              filter === "all"
                ? "Create the first milestone to get started."
                : "No milestones match this filter. Switch back to All to see the full list."
            }
            action={
              filter === "all" ? (
                <Can permission={WRITE}>
                  <Button onClick={() => setCreating(true)}>New milestone</Button>
                </Can>
              ) : (
                <Button variant="outline" onClick={() => setFilter("all")}>
                  Show all
                </Button>
              )
            }
          />
        ) : (
          <ul className="flex flex-col divide-y divide-line/70">
            {rows.map((m) => (
              <MilestoneRow
                key={m.id}
                milestone={m}
                canWrite={canWrite}
                onEdit={() => setEditing(m)}
                onComplete={() => void complete.mutate(m)}
                onDelete={() => setDeleting(m)}
              />
            ))}
          </ul>
        )}
      </Card>

      {creating ? (
        <MilestoneFormDialog
          milestone={null}
          projects={projects}
          members={members}
          onClose={() => setCreating(false)}
          onSaved={(_milestone, message) => {
            toast.success(message);
            milestonesState.reload();
            projectsState.reload();
          }}
        />
      ) : null}
      {editing ? (
        <MilestoneFormDialog
          milestone={editing}
          projects={projects}
          members={members}
          onClose={() => setEditing(null)}
          onSaved={(_milestone, message) => {
            setEditing(null);
            toast.success(message);
            milestonesState.reload();
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
        title={deleting ? `Delete ${deleting.title}?` : "Delete this milestone?"}
        body={`${deleting?.title} is removed from ${deleting?.project_name ?? "its project"}. This can't be undone.`}
        confirmLabel="Delete"
        pendingLabel="Deleting…"
        pending={remove.pending}
        {...(remove.error ? { error: problemMessage(remove.error) } : {})}
      />
    </div>
  );
}
