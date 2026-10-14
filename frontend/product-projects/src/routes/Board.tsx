import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { deleteTask, getAllMembers, getAllProjects, getAllTasks, updateTask } from "@/api/client";
import type { MemberOut, ProjectOut, TaskOut, TaskStage } from "@/api/types";
import { STAGE_LABEL } from "@/data/labels";
import { daysBetween, formatDateShort, today } from "@/lib/format";
import { Can, problemMessage, useAsync, useCan, useMutation } from "@opsgrids/client";
import {
  Announcer,
  Avatar,
  Button,
  Card,
  ClockIcon,
  ColumnsIcon,
  ConfirmDialog,
  EmptyState,
  ErrorState,
  KanbanBoard,
  PageHeader,
  PlusIcon,
  Select,
  Skeleton,
  cn,
  useAnnounce,
  useToast,
} from "@opsgrids/ui";
import type { KanbanCardState, KanbanColumn, SelectOption } from "@opsgrids/ui";
import { PriorityPill } from "@/ui";
import { TaskFormDialog } from "./TaskFormDialog";

const COLUMN_ORDER: TaskStage[] = ["backlog", "in_progress", "review", "done"];
const STAGE_TONE: Record<TaskStage, KanbanColumn["tone"]> = {
  backlog: "neutral",
  in_progress: "info",
  review: "warning",
  done: "success",
};
const WRITE = "projects.board.task.write";

/** One card's contents; the shared board draws the card and its move menu around them. */
function TaskCard({ task, now, state }: { task: TaskOut; now: string; state: KanbanCardState }) {
  const overdue = task.due_date !== null && task.stage !== "done" && daysBetween(now, task.due_date) < 0;
  return (
    <>
      <div className="flex items-center justify-between gap-2">
        <span className="inline-flex h-5 items-center rounded-full bg-ground px-2 text-caption font-bold tnum text-muted">
          {task.project_code}
        </span>
        <div className="flex items-center gap-1">
          <PriorityPill priority={task.priority} />
          {state.actions}
        </div>
      </div>

      <p className="text-sm font-semibold leading-snug text-ink">{task.title}</p>

      <div className="flex items-center justify-between gap-2">
        <span className="inline-flex min-w-0 items-center gap-2 text-xs text-muted">
          <Avatar name={task.assignee.name} size="xs" />
          <span className="max-w-32 truncate">{task.assignee.name}</span>
        </span>
        <div className="flex shrink-0 items-center gap-2">
          {task.due_date ? (
            <span
              className={cn(
                "inline-flex items-center gap-1 text-caption font-semibold tnum",
                overdue ? "text-danger" : "text-faint",
              )}
            >
              <ClockIcon className="size-3.5" />
              {formatDateShort(task.due_date)}
            </span>
          ) : null}
          <span className="inline-flex h-5 items-center rounded-full bg-accent-soft px-2 text-caption font-bold tnum text-accent-ink">
            {task.points}
          </span>
        </div>
      </div>
    </>
  );
}

interface BoardData {
  tasks: TaskOut[];
  projects: ProjectOut[];
  members: MemberOut[];
}

/** The four column headers previewed while the board loads, so nothing shifts on resolve. */
function BoardSkeleton() {
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
      {COLUMN_ORDER.map((col) => (
        <div key={col} className="flex flex-col gap-3 rounded-card bg-ground/60 p-3">
          <span className="text-sm font-bold text-ink">{STAGE_LABEL[col]}</span>
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-28 w-full rounded-tile" />
          ))}
        </div>
      ))}
    </div>
  );
}

export function BoardPage() {
  return (
    <Announcer>
      <BoardContent />
    </Announcer>
  );
}

function BoardContent() {
  const toast = useToast();
  const announce = useAnnounce();
  const [projectFilter, setProjectFilter] = useState("");
  const [assigneeFilter, setAssigneeFilter] = useState("");
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<TaskOut | null>(null);
  const [deleting, setDeleting] = useState<TaskOut | null>(null);

  const loadBoard = useCallback(async (): Promise<BoardData> => {
    const [tasks, projects, members] = await Promise.all([
      getAllTasks(projectFilter ? { project_code: projectFilter } : {}),
      getAllProjects(),
      getAllMembers(),
    ]);
    return { tasks, projects, members };
  }, [projectFilter]);

  const state = useAsync(loadBoard, [projectFilter]);

  // The board's own copy of the tasks, so a drag can patch it optimistically without waiting for
  // the reload a Create or Delete still goes through.
  const [tasks, setTasks] = useState<TaskOut[]>([]);
  useEffect(() => {
    if (state.status === "success") setTasks(state.data.tasks);
  }, [state.status, state.data]);

  const visibleTasks = useMemo(
    () => (assigneeFilter ? tasks.filter((t) => t.assignee.id === assigneeFilter) : tasks),
    [tasks, assigneeFilter],
  );

  const previousTasksRef = useRef<TaskOut[]>([]);

  const moveTask = useMutation(
    ({ id, stage }: { id: string; stage: TaskStage }) => updateTask(id, { stage }),
    {
      onSuccess: (updated) => {
        setTasks((prev) => prev.map((t) => (t.id === updated.id ? updated : t)));
        announce(`${updated.title} moved to ${STAGE_LABEL[updated.stage]}.`);
      },
      onError: (error) => {
        setTasks(previousTasksRef.current);
        toast.error("Couldn't move that card", { description: problemMessage(error) });
        announce(`The move failed — the card is back where it was: ${problemMessage(error)}`);
      },
    },
  );

  // The board announces "Moving…"; the outcome is announced above, with the server's own reason.
  async function handleMove(task: TaskOut, to: string) {
    const stage = to as TaskStage;
    previousTasksRef.current = tasks;
    setTasks((prev) => prev.map((t) => (t.id === task.id ? { ...t, stage } : t)));
    await moveTask.mutate({ id: task.id, stage });
  }

  const remove = useMutation((task: TaskOut) => deleteTask(task.id), {
    onSuccess: (_result, task) => {
      setDeleting(null);
      toast.success(`${task.title} deleted.`);
      state.reload();
    },
  });

  const canWrite = useCan(WRITE);
  const projectOptions: SelectOption[] = [
    { value: "", label: "All projects" },
    ...(state.status === "success" ? state.data.projects.map((p) => ({ value: p.code, label: `${p.code} · ${p.name}` })) : []),
  ];
  const assigneeOptions: SelectOption[] = [
    { value: "", label: "Everyone" },
    ...(state.status === "success" ? state.data.members.map((m) => ({ value: m.id, label: m.name })) : []),
  ];
  const total = state.status === "success" ? visibleTasks.length : 0;

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Workflow"
        title="Board"
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <span className="rounded-full bg-surface shadow-card px-4 h-11 inline-flex items-center gap-2 text-sm font-semibold">
              <ColumnsIcon className="size-4 text-muted" />
              <span className="tnum">{total}</span>
              <span className="text-muted font-medium">cards</span>
            </span>
            <Can permission={WRITE}>
              <Button leadingIcon={<PlusIcon className="size-4" />} onClick={() => setCreating(true)}>
                New card
              </Button>
            </Can>
          </div>
        }
      />

      {state.status === "success" ? (
        <div className="flex flex-wrap items-center gap-3">
          <Select
            label="Filter by project"
            containerClassName="w-56"
            value={projectFilter}
            onChange={(e) => setProjectFilter(e.target.value)}
            options={projectOptions}
          />
          <Select
            label="Filter by assignee"
            containerClassName="w-56"
            value={assigneeFilter}
            onChange={(e) => setAssigneeFilter(e.target.value)}
            options={assigneeOptions}
          />
        </div>
      ) : null}

      <p className="text-sm text-muted -mt-2">
        Drag a card between columns on a pointer device, or use its menu — the menu is the only way
        to move a card on a touchscreen. Every move is announced for screen readers.
      </p>

      {state.status === "loading" ? <BoardSkeleton /> : null}
      {state.status === "error" ? (
        <Card className="p-6">
          <ErrorState
            title="Couldn't load the board"
            message={problemMessage(state.error)}
            action={<Button onClick={state.reload}>Try again</Button>}
          />
        </Card>
      ) : null}
      {state.status === "success" ? (
        visibleTasks.length === 0 && !projectFilter && !assigneeFilter ? (
          <Card className="p-10">
            <EmptyState
              icon={<ColumnsIcon className="size-6" />}
              title="The board is empty"
              description="Create the first card to start tracking work."
              action={
                <Can permission={WRITE}>
                  <Button onClick={() => setCreating(true)}>New card</Button>
                </Can>
              }
            />
          </Card>
        ) : visibleTasks.length === 0 ? (
          <Card className="p-10">
            <EmptyState
              icon={<ColumnsIcon className="size-6" />}
              title="No cards match these filters"
              description="Clear the project or assignee filter to see the rest of the board."
            />
          </Card>
        ) : (
          <KanbanBoard<TaskOut>
            columns={COLUMN_ORDER.map((stage) => ({
              id: stage,
              label: STAGE_LABEL[stage],
              tone: STAGE_TONE[stage],
              summary: `${visibleTasks.filter((t) => t.stage === stage).reduce((sum, t) => sum + t.points, 0)} pts`,
            }))}
            items={visibleTasks}
            getItemId={(task) => task.id}
            getColumnId={(task) => task.stage}
            renderCard={(task, cardState) => <TaskCard task={task} now={today()} state={cardState} />}
            canMove={() => true}
            onMove={handleMove}
            announce={(task, _from, to, result) =>
              result === "moving" ? `Moving ${task.title} to ${STAGE_LABEL[to as TaskStage]}…` : ""
            }
            moveMenuLabel={(task) => task.title}
            getExtraActions={(task) => [
              { label: "Edit", onSelect: () => setEditing(task) },
              { label: "Delete", tone: "danger", onSelect: () => setDeleting(task) },
            ]}
            readOnly={!canWrite}
          />
        )
      ) : null}

      {creating && state.status === "success" ? (
        <TaskFormDialog
          task={null}
          projects={state.data.projects}
          members={state.data.members}
          {...(projectFilter ? { defaultProjectCode: projectFilter } : {})}
          onClose={() => setCreating(false)}
          onSaved={(_task, message) => {
            toast.success(message);
            state.reload();
          }}
        />
      ) : null}
      {editing && state.status === "success" ? (
        <TaskFormDialog
          task={editing}
          projects={state.data.projects}
          members={state.data.members}
          onClose={() => setEditing(null)}
          onSaved={(_task, message) => {
            setEditing(null);
            toast.success(message);
            state.reload();
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
        title={deleting ? `Delete ${deleting.title}?` : "Delete this card?"}
        body="This card is removed from the board. This can't be undone."
        confirmLabel="Delete"
        pendingLabel="Deleting…"
        pending={remove.pending}
        {...(remove.error ? { error: problemMessage(remove.error) } : {})}
      />
    </div>
  );
}
