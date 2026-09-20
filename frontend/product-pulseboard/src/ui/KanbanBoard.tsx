import { useEffect, useMemo, useRef, useState } from "react";

import type { Person, Project, Task, TaskStatus } from "@/api/types";
import { Avatar, GripIcon, MoreHorizontalIcon, Tag, cn } from "@opsgrids/ui";
import { formatHours } from "@/lib/format";
import { PriorityPill } from "./StatusPill";
import { TASK_STATUS_META } from "./statusMeta";

const COLUMNS: TaskStatus[] = ["backlog", "in_progress", "review", "done"];

/** A single board card. Draggable for pointer users; a move menu covers the keyboard path. */
function TaskCard({
  task,
  assigneeName,
  projectCode,
  menuOpen,
  onToggleMenu,
  onMove,
  onDragStart,
  onDragEnd,
  dragging,
}: {
  task: Task;
  assigneeName: string;
  projectCode: string;
  menuOpen: boolean;
  onToggleMenu: () => void;
  onMove: (to: TaskStatus) => void;
  onDragStart: () => void;
  onDragEnd: () => void;
  dragging: boolean;
}) {
  const targets = COLUMNS.filter((c) => c !== task.status);
  return (
    <article
      draggable
      onDragStart={onDragStart}
      onDragEnd={onDragEnd}
      className={cn(
        "group relative flex flex-col gap-2.5 rounded-tile border border-line bg-surface p-3 shadow-card transition-all",
        "cursor-grab active:cursor-grabbing hover:border-accent/40",
        dragging && "opacity-50",
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="inline-flex items-center gap-1.5 text-xs font-semibold text-faint tnum">
          <GripIcon className="size-3.5 text-line group-hover:text-faint transition-colors" />
          {task.key}
        </span>
        <div className="flex items-center gap-1">
          <PriorityPill priority={task.priority} />
          <div className="relative">
            <button
              type="button"
              onClick={onToggleMenu}
              aria-haspopup="menu"
              aria-expanded={menuOpen}
              aria-label={`Move ${task.key}`}
              className="grid size-6 place-items-center rounded-full text-faint hover:bg-ground hover:text-ink transition-colors"
            >
              <MoreHorizontalIcon className="size-4" />
            </button>
            {menuOpen ? (
              <div
                role="menu"
                className="absolute right-0 top-full z-30 mt-1 w-40 rounded-tile bg-surface p-1 shadow-float"
              >
                <p className="px-2 py-1 text-[0.65rem] font-bold uppercase tracking-wide text-faint">
                  Move to
                </p>
                {targets.map((to) => (
                  <button
                    key={to}
                    type="button"
                    role="menuitem"
                    onClick={() => onMove(to)}
                    className="flex w-full items-center gap-2 rounded-md px-2 h-8 text-sm font-medium text-ink-soft hover:bg-ground transition-colors"
                  >
                    <span
                      className="size-2 rounded-full"
                      style={{ backgroundColor: TASK_STATUS_META[to].color }}
                      aria-hidden
                    />
                    {TASK_STATUS_META[to].label}
                  </button>
                ))}
              </div>
            ) : null}
          </div>
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
 * keyboard-reachable "Move to" menu, and each move is announced to assistive tech.
 */
export function KanbanBoard({
  tasks: seed,
  people,
  projects,
}: {
  tasks: Task[];
  people: Person[];
  projects: Project[];
}) {
  const [tasks, setTasks] = useState<Task[]>(seed);
  const [dragId, setDragId] = useState<string | null>(null);
  const [overCol, setOverCol] = useState<TaskStatus | null>(null);
  const [menuFor, setMenuFor] = useState<string | null>(null);
  const [announce, setAnnounce] = useState("");
  const boardRef = useRef<HTMLDivElement>(null);

  const assigneeNameById = useMemo(
    () => new Map(people.map((p) => [p.id, p.name])),
    [people],
  );
  const projectCodeById = useMemo(
    () => new Map(projects.map((p) => [p.id, p.code])),
    [projects],
  );
  function assigneeName(id: string | null): string {
    if (id === null) return "Unassigned";
    return assigneeNameById.get(id) ?? "Unknown";
  }
  function projectCode(id: string): string {
    return projectCodeById.get(id) ?? "—";
  }

  useEffect(() => {
    if (menuFor === null) return;
    function onPointer(event: MouseEvent) {
      if (boardRef.current && !boardRef.current.contains(event.target as Node)) setMenuFor(null);
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") setMenuFor(null);
    }
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [menuFor]);

  const byColumn = useMemo(() => {
    const map: Record<TaskStatus, Task[]> = {
      backlog: [],
      in_progress: [],
      review: [],
      done: [],
    };
    for (const t of tasks) map[t.status].push(t);
    return map;
  }, [tasks]);

  function move(id: string, to: TaskStatus) {
    setTasks((prev) => {
      const task = prev.find((t) => t.id === id);
      if (!task || task.status === to) return prev;
      setAnnounce(`${task.key} moved to ${TASK_STATUS_META[to].label}`);
      return prev.map((t) => (t.id === id ? { ...t, status: to } : t));
    });
    setMenuFor(null);
  }

  return (
    <div ref={boardRef}>
      <div className="flex gap-4 overflow-x-auto pb-2">
        {COLUMNS.map((col) => {
          const meta = TASK_STATUS_META[col];
          const items = byColumn[col];
          const isOver = overCol === col && dragId !== null;
          return (
            <section
              key={col}
              aria-label={meta.label}
              onDragOver={(e) => {
                e.preventDefault();
                setOverCol(col);
              }}
              onDragLeave={(e) => {
                if (e.currentTarget === e.target) setOverCol(null);
              }}
              onDrop={(e) => {
                e.preventDefault();
                if (dragId) move(dragId, col);
                setOverCol(null);
                setDragId(null);
              }}
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
                    menuOpen={menuFor === task.id}
                    onToggleMenu={() => setMenuFor((cur) => (cur === task.id ? null : task.id))}
                    onMove={(to) => move(task.id, to)}
                    onDragStart={() => {
                      setDragId(task.id);
                      setMenuFor(null);
                    }}
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
