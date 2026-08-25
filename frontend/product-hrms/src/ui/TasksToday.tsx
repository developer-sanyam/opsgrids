import { useId, useState } from "react";

import type { TaskItem } from "@/api/types";
import { CheckSquareIcon, PlusIcon, SquareIcon, cn } from "@atmora/ui";

/**
 * Today's task checklist. The checkboxes toggle real local state and a small inline form appends
 * a task, so every control does something; the seed rows are sample until a task service exists.
 */
export function TasksToday({ tasks: seed }: { tasks: TaskItem[] }) {
  const [tasks, setTasks] = useState<TaskItem[]>(seed);
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState("");
  const inputId = useId();

  const done = tasks.filter((t) => t.done).length;

  function toggle(id: string) {
    setTasks((prev) =>
      prev.map((t) =>
        t.id === id ? { ...t, done: !t.done, due: !t.done ? "Done" : "Today" } : t,
      ),
    );
  }

  function submit(event: React.FormEvent) {
    event.preventDefault();
    const title = draft.trim();
    if (!title) return;
    setTasks((prev) => [
      { id: `local-${Date.now()}`, title, due: "Today", done: false },
      ...prev,
    ]);
    setDraft("");
    setAdding(false);
  }

  return (
    <div className="flex flex-col gap-3">
      <p className="text-sm font-medium text-muted" aria-live="polite">
        <span className="tnum font-semibold text-ink">{done}</span> of{" "}
        <span className="tnum">{tasks.length}</span> done
      </p>

      <ul className="flex flex-col">
        {tasks.map((task) => (
          <li key={task.id}>
            <button
              type="button"
              onClick={() => toggle(task.id)}
              aria-pressed={task.done}
              className="flex w-full items-center gap-3 rounded-tile px-2 py-2.5 text-left hover:bg-ground transition-colors"
            >
              {task.done ? (
                <CheckSquareIcon className="size-5 shrink-0 text-accent" />
              ) : (
                <SquareIcon className="size-5 shrink-0 text-faint" />
              )}
              <span
                className={cn(
                  "flex-1 text-sm",
                  task.done ? "text-faint line-through" : "text-ink-soft",
                )}
              >
                {task.title}
              </span>
              <span
                className={cn(
                  "shrink-0 rounded-full px-2 py-0.5 text-xs font-semibold tnum",
                  task.done ? "bg-present-soft text-present" : "bg-ground text-muted",
                )}
              >
                {task.due}
              </span>
            </button>
          </li>
        ))}
      </ul>

      {adding ? (
        <form onSubmit={submit} className="flex items-center gap-2">
          <label htmlFor={inputId} className="sr-only">
            New task title
          </label>
          <input
            id={inputId}
            autoFocus
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="What needs doing?"
            className="h-10 min-w-0 flex-1 rounded-full bg-ground px-4 text-sm text-ink placeholder:text-faint focus:outline-none"
          />
          <button
            type="submit"
            className="h-10 shrink-0 rounded-full bg-accent px-4 text-sm font-semibold text-white hover:bg-accent-strong transition-colors"
          >
            Add
          </button>
        </form>
      ) : (
        <button
          type="button"
          onClick={() => setAdding(true)}
          className="flex items-center justify-center gap-1.5 rounded-tile border border-dashed border-line px-3 h-10 text-sm font-semibold text-muted hover:border-accent hover:text-accent transition-colors"
        >
          <PlusIcon className="size-4" />
          Add new task
        </button>
      )}
    </div>
  );
}
