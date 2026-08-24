import { useMemo, useState } from "react";

import { NOW } from "@/api/fixtures";
import type { TaskOut, TaskStage } from "@/api/types";
import { STAGE_LABEL } from "@/data/labels";
import { cn } from "@/lib/cn";
import { daysBetween, formatDateShort } from "@/lib/format";
import { Avatar } from "./Avatar";
import { PriorityPill } from "./ProjectPills";
import { ChevronRight, ClockIcon } from "./icons";

const STAGES: { key: TaskStage; dot: string }[] = [
  { key: "backlog", dot: "var(--color-faint)" },
  { key: "in_progress", dot: "var(--color-accent)" },
  { key: "review", dot: "var(--color-cyan)" },
  { key: "done", dot: "var(--color-present)" },
];

const ORDER: TaskStage[] = STAGES.map((s) => s.key);

function TaskCard({
  task,
  onMove,
}: {
  task: TaskOut;
  onMove: (id: string, dir: -1 | 1) => void;
}) {
  const index = ORDER.indexOf(task.stage);
  const overdue =
    task.due_date !== null && task.stage !== "done" && daysBetween(NOW, task.due_date) < 0;

  return (
    <article className="group rounded-tile border border-line bg-surface p-3 shadow-card transition-shadow hover:shadow-float">
      <div className="flex items-center justify-between gap-2">
        <span className="rounded-full bg-ground px-2 py-0.5 text-[0.7rem] font-bold tnum text-muted">
          {task.project_code}
        </span>
        <PriorityPill priority={task.priority} />
      </div>

      <p className="mt-2 text-sm font-semibold leading-snug text-ink">{task.title}</p>

      <div className="mt-3 flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Avatar name={task.assignee.name} size="xs" />
          <span className="truncate text-xs text-muted">{task.assignee.name}</span>
        </div>
        <div className="flex items-center gap-2">
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

      {/* Move controls — keyboard reachable, appear on hover/focus-within. */}
      <div className="mt-2 flex items-center justify-end gap-1 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
        <button
          type="button"
          onClick={() => onMove(task.id, -1)}
          disabled={index === 0}
          aria-label={`Move ${task.title} to previous stage`}
          className="grid size-6 place-items-center rounded-full text-muted hover:bg-ground hover:text-ink disabled:opacity-30 disabled:hover:bg-transparent transition-colors"
        >
          <ChevronRight className="size-4 rotate-180" />
        </button>
        <button
          type="button"
          onClick={() => onMove(task.id, 1)}
          disabled={index === ORDER.length - 1}
          aria-label={`Move ${task.title} to next stage`}
          className="grid size-6 place-items-center rounded-full text-muted hover:bg-ground hover:text-ink disabled:opacity-30 disabled:hover:bg-transparent transition-colors"
        >
          <ChevronRight className="size-4" />
        </button>
      </div>
    </article>
  );
}

/**
 * The workflow board: four stage columns from Backlog to Done. Cards carry their project, title,
 * assignee, priority, due date and estimate; the per-card move controls actually reassign a card's
 * stage in local state, and each column header keeps a live count and point total.
 */
export function KanbanBoard({ tasks: seed }: { tasks: TaskOut[] }) {
  const [tasks, setTasks] = useState<TaskOut[]>(seed);

  function move(id: string, dir: -1 | 1) {
    setTasks((prev) =>
      prev.map((t) => {
        if (t.id !== id) return t;
        const idx = ORDER.indexOf(t.stage);
        const next = ORDER[Math.min(Math.max(idx + dir, 0), ORDER.length - 1)];
        return next ? { ...t, stage: next } : t;
      }),
    );
  }

  const byStage = useMemo(() => {
    const map = new Map<TaskStage, TaskOut[]>();
    for (const stage of ORDER) map.set(stage, []);
    for (const task of tasks) map.get(task.stage)?.push(task);
    return map;
  }, [tasks]);

  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
      {STAGES.map(({ key, dot }) => {
        const columnTasks = byStage.get(key) ?? [];
        const points = columnTasks.reduce((sum, t) => sum + t.points, 0);
        return (
          <section
            key={key}
            aria-label={STAGE_LABEL[key]}
            className="flex flex-col gap-3 rounded-card bg-ground/60 p-3"
          >
            <header className="flex items-center gap-2 px-1">
              <span className="size-2 rounded-full" style={{ backgroundColor: dot }} aria-hidden />
              <h3 className="text-sm font-bold text-ink">{STAGE_LABEL[key]}</h3>
              <span className="ml-auto flex items-center gap-2 text-xs font-semibold text-muted tnum">
                <span>{columnTasks.length}</span>
                <span className="rounded-full bg-surface px-1.5 py-0.5 text-faint">{points} pts</span>
              </span>
            </header>

            <div className="flex flex-col gap-2.5">
              {columnTasks.length === 0 ? (
                <p className="rounded-tile border border-dashed border-line px-3 py-6 text-center text-xs text-faint">
                  Nothing here
                </p>
              ) : (
                columnTasks.map((task) => (
                  <TaskCard key={task.id} task={task} onMove={move} />
                ))
              )}
            </div>
          </section>
        );
      })}
    </div>
  );
}
