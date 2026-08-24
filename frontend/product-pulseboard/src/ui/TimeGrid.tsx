import { useMemo, useState } from "react";

import type { Person, TimesheetRow, TimesheetStatus } from "@/api/types";
import { cn } from "@/lib/cn";
import { formatDayMonth, formatHours } from "@/lib/format";
import { Avatar } from "./Avatar";
import { CheckCircleIcon } from "./icons";

const STATUS_META: Record<TimesheetStatus, { label: string; tone: string }> = {
  draft: { label: "Draft", tone: "bg-ground text-muted" },
  submitted: { label: "Submitted", tone: "bg-info-soft text-info" },
  approved: { label: "Approved", tone: "bg-present-soft text-present" },
};

function weekdayLabel(iso: string): string {
  return new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-GB", {
    weekday: "short",
    timeZone: "UTC",
  });
}

interface TimeGridProps {
  rows: TimesheetRow[];
  people: Person[];
  weekDates: string[];
  today: string;
}

/**
 * The weekly time grid: people down the side, the seven days across the top, and entered / billable
 * hours in each cell. Each row carries its own approval affordance — a draft is submitted, a
 * submitted sheet is approved — driven by real local state so every control does something.
 */
export function TimeGrid({ rows, people, weekDates, today }: TimeGridProps) {
  const [statuses, setStatuses] = useState<Record<string, TimesheetStatus>>(() =>
    Object.fromEntries(rows.map((r) => [r.person_id, r.status])),
  );

  const personById = useMemo(
    () => new Map(people.map((p) => [p.id, p])),
    [people],
  );

  function advance(personId: string) {
    setStatuses((prev) => {
      const cur = prev[personId] ?? "draft";
      const next: TimesheetStatus =
        cur === "draft" ? "submitted" : cur === "submitted" ? "approved" : "approved";
      return { ...prev, [personId]: next };
    });
  }

  // Column totals across the whole team, entered and billable.
  const dayTotals = weekDates.map((_, dayIndex) => {
    let entered = 0;
    let billable = 0;
    for (const row of rows) {
      const cell = row.cells[dayIndex];
      if (cell) {
        entered += cell.entered_hours;
        billable += cell.billable_hours;
      }
    }
    return { entered, billable };
  });
  const grandEntered = dayTotals.reduce((s, d) => s + d.entered, 0);
  const grandBillable = dayTotals.reduce((s, d) => s + d.billable, 0);

  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-sm">
        <caption className="sr-only">
          Weekly timesheet — entered and billable hours per person per day.
        </caption>
        <thead>
          <tr className="border-b border-line text-xs font-semibold uppercase tracking-wide text-faint">
            <th scope="col" className="sticky left-0 z-10 bg-surface py-3 pr-3 text-left min-w-[180px]">
              Person
            </th>
            {weekDates.map((iso) => {
              const isToday = iso === today;
              return (
                <th
                  key={iso}
                  scope="col"
                  className={cn(
                    "px-2 py-3 text-center tnum",
                    isToday ? "text-accent" : "text-faint",
                  )}
                >
                  <span className="block">{weekdayLabel(iso)}</span>
                  <span className="block text-[0.65rem] font-medium normal-case">
                    {formatDayMonth(iso)}
                  </span>
                </th>
              );
            })}
            <th scope="col" className="px-3 py-3 text-right">Total</th>
            <th scope="col" className="py-3 pl-3 text-right min-w-[150px]">Status</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const person = personById.get(row.person_id);
            const status = statuses[row.person_id] ?? "draft";
            const rowEntered = row.cells.reduce((s, c) => s + c.entered_hours, 0);
            const rowBillable = row.cells.reduce((s, c) => s + c.billable_hours, 0);
            const meta = STATUS_META[status];
            return (
              <tr key={row.person_id} className="border-b border-line/70 last:border-0">
                <th scope="row" className="sticky left-0 z-10 bg-surface py-3 pr-3 text-left font-normal">
                  <div className="flex items-center gap-2.5">
                    <Avatar name={person?.name ?? "Unknown"} size="sm" />
                    <div className="flex min-w-0 flex-col leading-tight">
                      <span className="truncate font-semibold text-ink">{person?.name}</span>
                      <span className="truncate text-xs text-muted">
                        {formatHours(person?.capacity_hours ?? 0)} capacity
                      </span>
                    </div>
                  </div>
                </th>
                {row.cells.map((cell) => {
                  const empty = cell.entered_hours === 0;
                  const isToday = cell.date === today;
                  return (
                    <td
                      key={cell.date}
                      className={cn(
                        "px-2 py-3 text-center align-middle",
                        isToday && "bg-accent-soft/30",
                      )}
                    >
                      {empty ? (
                        <span className="text-faint">·</span>
                      ) : (
                        <div className="flex flex-col leading-tight">
                          <span className="tnum font-semibold text-ink">
                            {formatHours(cell.entered_hours)}
                          </span>
                          <span className="tnum text-[0.7rem] font-medium text-accent">
                            {formatHours(cell.billable_hours)} bill
                          </span>
                        </div>
                      )}
                    </td>
                  );
                })}
                <td className="px-3 py-3 text-right align-middle">
                  <div className="flex flex-col leading-tight">
                    <span className="tnum font-bold text-ink">{formatHours(rowEntered)}</span>
                    <span className="tnum text-[0.7rem] font-medium text-accent">
                      {formatHours(rowBillable)} bill
                    </span>
                  </div>
                </td>
                <td className="py-3 pl-3 text-right align-middle">
                  <div className="flex items-center justify-end gap-2">
                    <span
                      className={cn(
                        "inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs font-semibold",
                        meta.tone,
                      )}
                    >
                      {status === "approved" ? <CheckCircleIcon className="size-3.5" /> : null}
                      {meta.label}
                    </span>
                    {status !== "approved" ? (
                      <button
                        type="button"
                        onClick={() => advance(row.person_id)}
                        className={cn(
                          "rounded-full px-3 h-8 text-xs font-semibold transition-colors",
                          status === "draft"
                            ? "bg-ink text-white hover:bg-ink-soft"
                            : "bg-accent text-white hover:bg-accent-strong",
                        )}
                      >
                        {status === "draft" ? "Submit" : "Approve"}
                      </button>
                    ) : null}
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
        <tfoot>
          <tr className="border-t-2 border-line font-semibold">
            <th scope="row" className="sticky left-0 z-10 bg-surface py-3 pr-3 text-left text-sm">
              Team total
            </th>
            {dayTotals.map((d, i) => (
              <td key={weekDates[i]} className="px-2 py-3 text-center align-middle">
                <span className="tnum text-sm text-ink">{formatHours(d.entered)}</span>
              </td>
            ))}
            <td className="px-3 py-3 text-right align-middle">
              <div className="flex flex-col leading-tight">
                <span className="tnum text-sm text-ink">{formatHours(grandEntered)}</span>
                <span className="tnum text-[0.7rem] font-medium text-accent">
                  {formatHours(grandBillable)} bill
                </span>
              </div>
            </td>
            <td className="py-3" />
          </tr>
        </tfoot>
      </table>
    </div>
  );
}
