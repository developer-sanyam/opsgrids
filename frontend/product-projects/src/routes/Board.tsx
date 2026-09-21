import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { deleteTask, getAllMembers, getAllProjects, getAllTasks, updateTask } from "@/api/client";
import type { MemberOut, ProjectOut, TaskOut, TaskStage } from "@/api/types";
import { STAGE_LABEL } from "@/data/labels";
import { today } from "@/lib/format";
import { Can, problemMessage, useAsync, useCan, useMutation } from "@opsgrids/client";
import {
  Announcer,
  Button,
  Card,
  ColumnsIcon,
  ConfirmDialog,
  EmptyState,
  ErrorState,
  PageHeader,
  PlusIcon,
  Select,
  Skeleton,
  useAnnounce,
  useToast,
} from "@opsgrids/ui";
import type { SelectOption } from "@opsgrids/ui";
import { KanbanBoard } from "@/ui";
import { TaskFormDialog } from "./TaskFormDialog";

const COLUMN_ORDER: TaskStage[] = ["backlog", "in_progress", "review", "done"];
const WRITE = "projects.board.task.write";

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
  const [movingId, setMovingId] = useState<string | null>(null);

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
        setMovingId(null);
      },
      onError: (error) => {
        setTasks(previousTasksRef.current);
        toast.error("Couldn't move that card", { description: problemMessage(error) });
        announce(`The move failed — the card is back where it was: ${problemMessage(error)}`);
        setMovingId(null);
      },
    },
  );

  function handleMove(task: TaskOut, to: TaskStage) {
    previousTasksRef.current = tasks;
    setMovingId(task.id);
    setTasks((prev) => prev.map((t) => (t.id === task.id ? { ...t, stage: to } : t)));
    void moveTask.mutate({ id: task.id, stage: to });
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
          <div className="flex items-center gap-2">
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
          <KanbanBoard
            tasks={visibleTasks}
            now={today()}
            canWrite={canWrite}
            movingId={movingId}
            onMove={handleMove}
            onEdit={setEditing}
            onDelete={setDeleting}
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
