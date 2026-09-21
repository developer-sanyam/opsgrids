import { useMemo, useState } from "react";

import type { TaskOut, TaskStage } from "@/api/types";
import { STAGE_LABEL } from "@/data/labels";
import { daysBetween, formatDateShort } from "@/lib/format";
import { Avatar, ClockIcon, RowActions, cn } from "@opsgrids/ui";
import { PriorityPill } from "./ProjectPills";

const STAGES: { key: TaskStage; dot: string }[] = [
  { key: "backlog", dot: "var(--color-faint)" },
  { key: "in_progress", dot: "var(--color-accent)" },
  { key: "review", dot: "var(--color-cyan)" },
  { key: "done", dot: "var(--color-present)" },
];

const ORDER: TaskStage[] = STAGES.map((s) => s.key);

/** A single board card. Draggable for pointer users; every move is also reachable from the row
 * actions menu — HTML5 drag-and-drop never fires on touch, so that menu is the only path on a
 * phone, not a fallback for people who can't use a mouse. */
function TaskCard({
  task,
  now,
  canWrite,
  moving,
  onMove,
  onEdit,
  onDelete,
  onDragStart,
  onDragEnd,
  dragging,
}: {
  task: TaskOut;
  now: string;
  canWrite: boolean;
  moving: boolean;
  onMove: (to: TaskStage) => void;
  onEdit: () => void;
  onDelete: () => void;
  onDragStart: () => void;
  onDragEnd: () => void;
  dragging: boolean;
}) {
  const overdue = task.due_date !== null && task.stage !== "done" && daysBetween(now, task.due_date) < 0;
  const targets = ORDER.filter((s) => s !== task.stage);

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
        <span className="rounded-full bg-ground px-2 py-0.5 text-[0.7rem] font-bold tnum text-muted">
          {task.project_code}
        </span>
        <div className="flex items-center gap-1.5">
          <PriorityPill priority={task.priority} />
          {canWrite ? (
            <RowActions
              label={task.title}
              inlineLimit={1}
              actions={[
                { label: "Edit", onSelect: onEdit },
                ...targets.map((to) => ({
                  label: `Move to ${STAGE_LABEL[to]}`,
                  onSelect: () => onMove(to),
                })),
                { label: "Delete", tone: "danger" as const, onSelect: onDelete },
              ]}
            />
          ) : null}
        </div>
      </div>

      <p className="text-sm font-semibold leading-snug text-ink">{task.title}</p>

      <div className="flex items-center justify-between gap-2">
        <span className="inline-flex items-center gap-1.5 text-xs text-muted min-w-0">
          <Avatar name={task.assignee.name} size="xs" />
          <span className="truncate max-w-[8rem]">{task.assignee.name}</span>
        </span>
        <div className="flex shrink-0 items-center gap-2">
          {task.due_date ? (
            <span
              className={cn(
                "inline-flex items-center gap-1 text-[0.7rem] font-semibold tnum",
                overdue ? "text-danger" : "text-faint",
              )}
            >
              <ClockIcon className="size-3.5" />
              {formatDateShort(task.due_date)}
            </span>
          ) : null}
          <span className="rounded-full bg-accent-soft px-1.5 py-0.5 text-[0.7rem] font-bold tnum text-accent-ink">
            {task.points}
          </span>
        </div>
      </div>
    </article>
  );
}

/**
 * The workflow board: four stage columns from Backlog to Done, over a horizontal scroll on
 * narrow screens. Cards drag between columns for pointer users; every card also carries a
 * keyboard-reachable row-actions menu with an explicit "Move to…" entry per other stage, so a
 * move is never mouse-only. Purely a view over `tasks` — the optimistic patch, its rollback and
 * the real `PATCH`/`DELETE` calls live in `BoardPage`, the one place that knows whether the write
 * actually landed.
 */
export function KanbanBoard({
  tasks,
  now,
  canWrite,
  movingId,
  onMove,
  onEdit,
  onDelete,
}: {
  tasks: TaskOut[];
  now: string;
  canWrite: boolean;
  /** The id of the card currently mid-move, if any — dims it and marks it busy. */
  movingId: string | null;
  onMove: (task: TaskOut, to: TaskStage) => void;
  onEdit: (task: TaskOut) => void;
  onDelete: (task: TaskOut) => void;
}) {
  const [dragId, setDragId] = useState<string | null>(null);
  const [overCol, setOverCol] = useState<TaskStage | null>(null);
  const [announce, setAnnounce] = useState("");

  const byStage = useMemo(() => {
    const map = new Map<TaskStage, TaskOut[]>();
    for (const stage of ORDER) map.set(stage, []);
    for (const task of tasks) map.get(task.stage)?.push(task);
    return map;
  }, [tasks]);

  function move(task: TaskOut, to: TaskStage) {
    if (task.stage === to) return;
    setAnnounce(`Moving ${task.title} to ${STAGE_LABEL[to]}…`);
    onMove(task, to);
  }

  return (
    <div>
      <div className="flex gap-4 overflow-x-auto pb-2">
        {STAGES.map(({ key, dot }) => {
          const columnTasks = byStage.get(key) ?? [];
          const points = columnTasks.reduce((sum, t) => sum + t.points, 0);
          const isOver = overCol === key && dragId !== null;
          return (
            <section
              key={key}
              aria-label={STAGE_LABEL[key]}
              onDragOver={
                canWrite
                  ? (e) => {
                      e.preventDefault();
                      setOverCol(key);
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
                      if (task) move(task, key);
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
                <span className="size-2.5 rounded-full" style={{ backgroundColor: dot }} aria-hidden />
                <h3 className="text-sm font-bold text-ink">{STAGE_LABEL[key]}</h3>
                <span className="ml-auto flex items-center gap-2 text-xs font-semibold text-muted tnum">
                  <span>{columnTasks.length}</span>
                  <span className="rounded-full bg-surface px-1.5 py-0.5 text-faint shadow-card">
                    {points} pts
                  </span>
                </span>
              </header>

              <div className="flex flex-col gap-2.5">
                {columnTasks.map((task) => (
                  <TaskCard
                    key={task.id}
                    task={task}
                    now={now}
                    canWrite={canWrite}
                    moving={movingId === task.id}
                    onMove={(to) => move(task, to)}
                    onEdit={() => onEdit(task)}
                    onDelete={() => onDelete(task)}
                    onDragStart={() => setDragId(task.id)}
                    onDragEnd={() => {
                      setDragId(null);
                      setOverCol(null);
                    }}
                    dragging={dragId === task.id}
                  />
                ))}
                {columnTasks.length === 0 ? (
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
