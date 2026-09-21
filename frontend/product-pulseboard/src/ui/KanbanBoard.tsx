import { useMemo, useState } from "react";

import type { Person, Project, Task, TaskStatus } from "@/api/types";
import { Avatar, RowActions, Tag, cn } from "@opsgrids/ui";
import { formatHours } from "@/lib/format";
import { PriorityPill } from "./StatusPill";
import { TASK_STATUS_META } from "./statusMeta";

const COLUMNS: TaskStatus[] = ["backlog", "in_progress", "review", "done"];

/** A single board card. Draggable for pointer users; every move is also reachable from the row
 * actions menu — HTML5 drag-and-drop never fires on touch, so that menu is the only path on a
 * phone, not a fallback for people who can't use a mouse. */
function TaskCard({
  task,
  assigneeName,
  projectCode,
  canWrite,
  moving,
  onMove,
  onDelete,
  onDragStart,
  onDragEnd,
  dragging,
}: {
  task: Task;
  assigneeName: string;
  projectCode: string;
  canWrite: boolean;
  moving: boolean;
  onMove: (to: TaskStatus) => void;
  onDelete: () => void;
  onDragStart: () => void;
  onDragEnd: () => void;
  dragging: boolean;
}) {
  const targets = COLUMNS.filter((c) => c !== task.status);
  return (
    <article
      draggable={canWrite}
      onDragStart={canWrite ? onDragStart : undefined}
      onDragEnd={canWrite ? onDragEnd : undefined}
      aria-busy={moving || undefined}
      className={cn(
        "group relative flex flex-col gap-2.5 rounded-tile border border-line bg-surface p-3 shadow-card transition-all",
        canWrite && "cursor-grab active:cursor-grabbing hover:border-accent/40",
        (dragging || moving) && "opacity-50",
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="inline-flex items-center gap-1.5 text-xs font-semibold text-faint tnum">
          {task.key}
        </span>
        <div className="flex items-center gap-1">
          <PriorityPill priority={task.priority} />
          {canWrite ? (
            <RowActions
              label={task.key}
              inlineLimit={1}
              actions={[
                ...targets.map((to) => ({
                  label: `Move to ${TASK_STATUS_META[to].label}`,
                  onSelect: () => onMove(to),
                })),
                { label: "Delete", tone: "danger" as const, onSelect: onDelete },
              ]}
            />
          ) : null}
        </div>
      </div>

      <p className="text-sm font-semibold leading-snug text-ink">{task.title}</p>

      {task.tags.length > 0 ? (
        <div className="flex flex-wrap gap-1.5">
          {task.tags.map((tag) => (
            <Tag key={tag} className="px-2 py-0.5 text-[0.68rem]">
              {tag}
            </Tag>
          ))}
        </div>
      ) : null}

      <div className="flex items-center justify-between border-t border-line pt-2.5">
        <span className="inline-flex items-center gap-1.5 text-xs text-muted">
          <Avatar name={assigneeName} size="xs" />
          <span className="truncate max-w-[8rem]">{assigneeName}</span>
        </span>
        <span className="flex items-center gap-2 text-xs font-semibold text-faint tnum">
          <span>{projectCode}</span>
          <span className="rounded-full bg-ground px-2 py-0.5 text-ink-soft">
            {formatHours(task.estimate_hours)}
          </span>
        </span>
      </div>
    </article>
  );
}

/**
 * The task board: four columns (Backlog → In Progress → Review → Done) over a horizontal scroll
 * on narrow screens. Cards drag between columns for pointer users; every card also carries a
 * keyboard-reachable row-actions menu, and each move is announced to assistive tech.
 *
 * Purely a view over `tasks` — the optimistic patch and its rollback live in `BoardPage`, which
 * is the one place that knows whether the write actually landed.
 */
export function KanbanBoard({
  tasks,
  people,
  projects,
  canWrite,
  movingKey,
  onMove,
  onDelete,
}: {
  tasks: Task[];
  people: Person[];
  projects: Project[];
  canWrite: boolean;
  /** The key of the card currently mid-move, if any — dims it and marks it busy. */
  movingKey: string | null;
  onMove: (task: Task, to: TaskStatus) => void;
  onDelete: (task: Task) => void;
}) {
  const [dragId, setDragId] = useState<string | null>(null);
  const [overCol, setOverCol] = useState<TaskStatus | null>(null);
  const [announce, setAnnounce] = useState("");

  const assigneeNameById = useMemo(() => new Map(people.map((p) => [p.id, p.name])), [people]);
  const projectCodeById = useMemo(() => new Map(projects.map((p) => [p.id, p.code])), [projects]);
  function assigneeName(id: string | null): string {
    if (id === null) return "Unassigned";
    return assigneeNameById.get(id) ?? "Unknown";
  }
  function projectCode(id: string): string {
    return projectCodeById.get(id) ?? "—";
  }

  const byColumn = useMemo(() => {
    const map: Record<TaskStatus, Task[]> = { backlog: [], in_progress: [], review: [], done: [] };
    for (const t of tasks) map[t.status].push(t);
    return map;
  }, [tasks]);

  function move(task: Task, to: TaskStatus) {
    if (task.status === to) return;
    setAnnounce(`Moving ${task.key} to ${TASK_STATUS_META[to].label}…`);
    onMove(task, to);
  }

  return (
    <div>
      <div className="flex gap-4 overflow-x-auto pb-2">
        {COLUMNS.map((col) => {
          const meta = TASK_STATUS_META[col];
          const items = byColumn[col];
          const isOver = overCol === col && dragId !== null;
          return (
            <section
              key={col}
              aria-label={meta.label}
              onDragOver={
                canWrite
                  ? (e) => {
                      e.preventDefault();
                      setOverCol(col);
                    }
                  : undefined
              }
              onDragLeave={
                canWrite
                  ? (e) => {
                      if (e.currentTarget === e.target) setOverCol(null);
                    }
                  : undefined
              }
              onDrop={
                canWrite
                  ? (e) => {
                      e.preventDefault();
                      const task = tasks.find((t) => t.id === dragId);
                      if (task) move(task, col);
                      setOverCol(null);
                      setDragId(null);
                    }
                  : undefined
              }
              className={cn(
                "flex w-[280px] shrink-0 flex-col gap-3 rounded-card p-3 transition-colors",
                isOver ? "bg-accent-soft/60 outline outline-2 outline-dashed outline-accent/50" : "bg-ground/60",
              )}
            >
              <header className="flex items-center gap-2 px-1">
                <span className="size-2.5 rounded-full" style={{ backgroundColor: meta.color }} aria-hidden />
                <h3 className="text-sm font-bold text-ink">{meta.label}</h3>
                <span className="ml-auto grid h-5 min-w-5 place-items-center rounded-full bg-surface px-1.5 text-xs font-bold text-muted tnum shadow-card">
                  {items.length}
                </span>
              </header>

              <div className="flex flex-col gap-2.5">
                {items.map((task) => (
                  <TaskCard
                    key={task.id}
                    task={task}
                    assigneeName={assigneeName(task.assignee_id)}
                    projectCode={projectCode(task.project_id)}
                    canWrite={canWrite}
                    moving={movingKey === task.key}
                    onMove={(to) => move(task, to)}
                    onDelete={() => onDelete(task)}
                    onDragStart={() => setDragId(task.id)}
                    onDragEnd={() => {
                      setDragId(null);
                      setOverCol(null);
                    }}
                    dragging={dragId === task.id}
                  />
                ))}
                {items.length === 0 ? (
                  <p className="rounded-tile border border-dashed border-line px-3 py-6 text-center text-xs text-faint">
                    Nothing here
                  </p>
                ) : null}
              </div>
            </section>
          );
        })}
      </div>
      <p className="sr-only" role="status" aria-live="polite">
        {announce}
      </p>
    </div>
  );
}
