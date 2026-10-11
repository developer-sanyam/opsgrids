import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { deleteTask, getPeople, getProjects, listTasks, updateTask } from "@/api/client";
import type { Person, Project, Task, TaskStatus } from "@/api/types";
import { Can, problemMessage, useAsync, useCan, useMutation } from "@opsgrids/client";
import {
  Announcer,
  Avatar,
  Button,
  Card,
  ColumnsIcon,
  ConfirmDialog,
  EmptyState,
  ErrorState,
  KanbanBoard,
  PageHeader,
  PlusIcon,
  Select,
  Skeleton,
  Tag,
  useAnnounce,
  useToast,
} from "@opsgrids/ui";
import type { KanbanCardState, KanbanColumn, SelectOption } from "@opsgrids/ui";
import { formatHours } from "@/lib/format";
import { PriorityPill, TASK_STATUS_META } from "@/ui";
import { TaskFormDialog } from "./TaskFormDialog";

const COLUMN_ORDER = ["backlog", "in_progress", "review", "done"] as const;
const STATUS_TONE: Record<TaskStatus, KanbanColumn["tone"]> = {
  backlog: "neutral",
  in_progress: "info",
  review: "warning",
  done: "success",
};
const COLUMNS: KanbanColumn[] = COLUMN_ORDER.map((status) => ({
  id: status,
  label: TASK_STATUS_META[status].label,
  tone: STATUS_TONE[status],
}));

/** One card's contents; the shared board draws the card and its move menu around them. */
function TaskCard({
  task,
  assigneeName,
  projectCode,
  state,
}: {
  task: Task;
  assigneeName: string;
  projectCode: string;
  state: KanbanCardState;
}) {
  return (
    <>
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-semibold text-faint tnum">{task.key}</span>
        <div className="flex items-center gap-1">
          <PriorityPill priority={task.priority} />
          {state.actions}
        </div>
      </div>

      <p className="text-sm font-semibold leading-snug text-ink">{task.title}</p>

      {task.tags.length > 0 ? (
        <div className="flex flex-wrap gap-2">
          {task.tags.map((tag) => (
            <Tag key={tag} className="px-2 text-caption">
              {tag}
            </Tag>
          ))}
        </div>
      ) : null}

      <div className="flex items-center justify-between border-t border-line pt-3">
        <span className="inline-flex items-center gap-2 text-xs text-muted">
          <Avatar name={assigneeName} size="xs" />
          <span className="max-w-32 truncate">{assigneeName}</span>
        </span>
        <span className="flex items-center gap-2 text-xs font-semibold text-faint tnum">
          <span>{projectCode}</span>
          <span className="inline-flex h-5 items-center rounded-full bg-ground px-2 text-ink-soft">
            {formatHours(task.estimate_hours)}
          </span>
        </span>
      </div>
    </>
  );
}

interface BoardData {
  tasks: Task[];
  people: Person[];
  projects: Project[];
}

/** The four column headers previewed while the board loads, so nothing shifts on resolve. */
function BoardSkeleton() {
  return (
    <div className="flex gap-4 overflow-x-auto pb-2">
      {COLUMN_ORDER.map((col) => (
        <div key={col} className="flex w-[280px] shrink-0 flex-col gap-3 rounded-card bg-ground/60 p-3">
          <div className="flex items-center gap-2 px-1">
            <span className="size-2.5 rounded-full" style={{ backgroundColor: TASK_STATUS_META[col].color }} aria-hidden />
            <span className="text-sm font-bold text-ink">{TASK_STATUS_META[col].label}</span>
          </div>
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
  const [deleting, setDeleting] = useState<Task | null>(null);

  const loadBoard = useCallback(async (): Promise<BoardData> => {
    const [tasksPage, people, projects] = await Promise.all([
      listTasks({
        project: projectFilter || undefined,
        assignee: assigneeFilter || undefined,
      }),
      getPeople(),
      getProjects(),
    ]);
    return { tasks: tasksPage.items, people, projects };
  }, [projectFilter, assigneeFilter]);

  const state = useAsync(loadBoard, [projectFilter, assigneeFilter]);

  // The board's own copy of the tasks, so a drag can patch it optimistically without waiting for
  // the reload a `Create` or `Delete` still goes through.
  const [tasks, setTasks] = useState<Task[]>([]);
  useEffect(() => {
    if (state.status === "success") setTasks(state.data.tasks);
  }, [state.status, state.data]);

  const previousTasksRef = useRef<Task[]>([]);

  const moveTask = useMutation(
    ({ key, status }: { key: string; status: TaskStatus }) => updateTask(key, { status }),
    {
      onSuccess: (updated) => {
        setTasks((prev) => prev.map((t) => (t.key === updated.key ? updated : t)));
        announce(`${updated.key} moved to ${TASK_STATUS_META[updated.status].label}.`);
      },
      onError: (error, input) => {
        setTasks(previousTasksRef.current);
        toast.error(`Couldn't move ${input.key}`, { description: problemMessage(error) });
        announce(`${input.key} is back where it was — the move failed.`);
      },
    },
  );

  // The board announces "Moving…"; the outcome is announced above.
  async function handleMove(task: Task, to: string) {
    const status = to as TaskStatus;
    previousTasksRef.current = tasks;
    setTasks((prev) => prev.map((t) => (t.key === task.key ? { ...t, status } : t)));
    await moveTask.mutate({ key: task.key, status });
  }

  const remove = useMutation((task: Task) => deleteTask(task.key), {
    onSuccess: (_result, task) => {
      setDeleting(null);
      toast.success(`${task.key} deleted.`);
      state.reload();
    },
  });

  const canWrite = useCan("pulseboard.board.task.write");
  const people = state.status === "success" ? state.data.people : null;
  const projects = state.status === "success" ? state.data.projects : null;
  const assigneeNameById = useMemo(() => new Map((people ?? []).map((p) => [p.id, p.name])), [people]);
  const projectCodeById = useMemo(() => new Map((projects ?? []).map((p) => [p.id, p.code])), [projects]);
  const projectOptions: SelectOption[] = [
    { value: "", label: "All engagements" },
    ...(state.status === "success" ? state.data.projects.map((p) => ({ value: p.code, label: p.code })) : []),
  ];
  const assigneeOptions: SelectOption[] = [
    { value: "", label: "Everyone" },
    ...(state.status === "success" ? state.data.people.map((p) => ({ value: p.id, label: p.name })) : []),
  ];
  const total = state.status === "success" ? tasks.length : 0;

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Delivery"
        title="Board"
        actions={
          <div className="flex items-center gap-2">
            <span className="rounded-full bg-surface shadow-card px-4 h-11 inline-flex items-center gap-2 text-sm font-semibold">
              <ColumnsIcon className="size-4 text-muted" />
              <span className="tnum">{total}</span>
              <span className="text-muted font-medium">tasks</span>
            </span>
            <Can permission="pulseboard.board.task.write">
              <Button leadingIcon={<PlusIcon className="size-4" />} onClick={() => setCreating(true)}>
                New task
              </Button>
            </Can>
          </div>
        }
      />

      {state.status === "success" ? (
        <div className="flex flex-wrap items-center gap-3">
          <Select
            label="Filter by engagement"
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
        tasks.length === 0 && !projectFilter && !assigneeFilter ? (
          <Card className="p-10">
            <EmptyState
              icon={<ColumnsIcon className="size-6" />}
              title="The board is empty"
              description="Create the first card to start tracking work."
              action={
                <Can permission="pulseboard.board.task.write">
                  <Button onClick={() => setCreating(true)}>New task</Button>
                </Can>
              }
            />
          </Card>
        ) : (
          <KanbanBoard<Task>
            columns={COLUMNS}
            items={tasks}
            getItemId={(task) => task.key}
            getColumnId={(task) => task.status}
            renderCard={(task, cardState) => (
              <TaskCard
                task={task}
                assigneeName={
                  task.assignee_id === null ? "Unassigned" : (assigneeNameById.get(task.assignee_id) ?? "Unknown")
                }
                projectCode={projectCodeById.get(task.project_id) ?? "—"}
                state={cardState}
              />
            )}
            canMove={() => true}
            onMove={handleMove}
            announce={(task, _from, to, result) =>
              result === "moving" ? `Moving ${task.key} to ${TASK_STATUS_META[to as TaskStatus].label}…` : ""
            }
            moveMenuLabel={(task) => task.key}
            getExtraActions={(task) => [{ label: "Delete", tone: "danger", onSelect: () => setDeleting(task) }]}
            readOnly={!canWrite}
          />
        )
      ) : null}

      {creating && state.status === "success" ? (
        <TaskFormDialog
          projects={state.data.projects}
          people={state.data.people}
          {...(projectFilter ? { defaultProjectCode: projectFilter } : {})}
          onClose={() => setCreating(false)}
          onSaved={(_task, message) => {
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
        title={deleting ? `Delete ${deleting.key}?` : "Delete this card?"}
        body={`${deleting?.title ?? "This card"} is removed from the board. This can't be undone.`}
        confirmLabel="Delete"
        pendingLabel="Deleting…"
        pending={remove.pending}
        {...(remove.error ? { error: problemMessage(remove.error) } : {})}
      />
    </div>
  );
}
