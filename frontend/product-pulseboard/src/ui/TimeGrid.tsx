import { useMemo } from "react";

import type { Person, TimesheetRow, TimesheetStatus } from "@/api/types";
import { Avatar, Button, CheckCircleIcon, cn } from "@opsgrids/ui";
import { formatDayMonth, formatHours } from "@/lib/format";

const STATUS_META: Record<TimesheetStatus, { label: string; tone: string }> = {
  draft: { label: "Draft", tone: "bg-ground text-muted" },
  submitted: { label: "Submitted", tone: "bg-info-soft text-info" },
  approved: { label: "Approved", tone: "bg-present-soft text-present" },
};

/** The one legal forward move for a status, or `null` once there is none (approved is terminal
 * going forward — it can only reopen). Mirrors `services/timesheet/app/domain.py`'s
 * `STATUS_TRANSITIONS`, which is the actual source of truth the server enforces. */
const NEXT_STATUS: Record<TimesheetStatus, TimesheetStatus | null> = {
  draft: "submitted",
  submitted: "approved",
  approved: null,
};

const NEXT_LABEL: Record<TimesheetStatus, string> = {
  draft: "Submit",
  submitted: "Approve",
  approved: "Approve",
};

function weekdayLabel(iso: string): string {
  return new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-GB", { weekday: "short", timeZone: "UTC" });
}

interface TimeGridProps {
  rows: TimesheetRow[];
  people: Person[];
  weekDates: string[];
  today: string;
  canWrite: boolean;
  /** The person whose row is mid-request, if any — disables just that row's controls. */
  busyPersonId: string | null;
  onAdvance: (row: TimesheetRow) => void;
  onReopen: (row: TimesheetRow) => void;
  onEditWeek: (row: TimesheetRow) => void;
}

/**
 * The weekly time grid: people down the side, the seven days across the top, and entered /
 * billable hours in each cell. Every row's Submit / Approve / Reopen calls the real
 * `POST /timesheets/status`; the grid only ever reflects what the server answered.
 */
export function TimeGrid({ rows, people, weekDates, today, canWrite, busyPersonId, onAdvance, onReopen, onEditWeek }: TimeGridProps) {
  const personById = useMemo(() => new Map(people.map((p) => [p.id, p])), [people]);

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
      <table className="w-full min-w-[880px] border-collapse text-sm">
        <caption className="sr-only">
          Weekly timesheet — entered and billable hours per person per day.
        </caption>
        <thead>
          <tr className="border-b border-line text-xs font-semibold uppercase tracking-wide text-faint">
            <th scope="col" className="sticky left-0 z-10 bg-surface py-3 pr-3 text-left min-w-[150px]">
              Person
            </th>
            {weekDates.map((iso) => {
              const isToday = iso === today;
              return (
                <th
                  key={iso}
                  scope="col"
                  className={cn("px-2 py-3 text-center tnum", isToday ? "text-accent" : "text-faint")}
                >
                  <span className="block">{weekdayLabel(iso)}</span>
                  <span className="block text-[0.65rem] font-medium normal-case">{formatDayMonth(iso)}</span>
                </th>
              );
            })}
            <th scope="col" className="px-3 py-3 text-right">Total</th>
            <th scope="col" className="py-3 pl-3 text-right min-w-[220px]">Status</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const person = personById.get(row.person_id);
            const rowEntered = row.cells.reduce((s, c) => s + c.entered_hours, 0);
            const rowBillable = row.cells.reduce((s, c) => s + c.billable_hours, 0);
            const meta = STATUS_META[row.status];
            const next = NEXT_STATUS[row.status];
            const busy = busyPersonId === row.person_id;
            const canReopen = row.status !== "draft";
            return (
              <tr key={row.person_id} className="border-b border-line/70 last:border-0">
                <th scope="row" className="sticky left-0 z-10 bg-surface py-3 pr-3 text-left font-normal">
                  <div className="flex items-center gap-2.5">
                    <Avatar name={person?.name ?? "Unknown"} size="sm" />
                    <div className="flex min-w-0 flex-col leading-tight">
                      <span className="truncate font-semibold text-ink">{person?.name ?? row.person_id}</span>
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
                    <td key={cell.date} className={cn("px-2 py-3 text-center align-middle", isToday && "bg-accent-soft/30")}>
                      {empty ? (
                        <span className="text-faint">·</span>
                      ) : (
                        <div className="flex flex-col leading-tight">
                          <span className="tnum font-semibold text-ink">{formatHours(cell.entered_hours)}</span>
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
                    <span className="tnum text-[0.7rem] font-medium text-accent">{formatHours(rowBillable)} bill</span>
                  </div>
                </td>
                <td className="py-3 pl-3 align-middle">
                  <div className="flex flex-col items-end gap-1.5">
                    <span className={cn("inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs font-semibold", meta.tone)}>
                      {row.status === "approved" ? <CheckCircleIcon className="size-3.5" /> : null}
                      {meta.label}
                    </span>
                    {canWrite ? (
                      <div className="flex flex-wrap items-center justify-end gap-1.5">
                        <Button variant="ghost" onClick={() => onEditWeek(row)} disabled={busy}>
                          Edit week
                        </Button>
                        {canReopen ? (
                          <Button variant="outline" onClick={() => onReopen(row)} loading={busy}>
                            Reopen
                          </Button>
                        ) : null}
                        {next ? (
                          <Button onClick={() => onAdvance(row)} loading={busy}>
                            {NEXT_LABEL[row.status]}
                          </Button>
                        ) : null}
                      </div>
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
                <span className="tnum text-[0.7rem] font-medium text-accent">{formatHours(grandBillable)} bill</span>
              </div>
            </td>
            <td className="py-3" />
          </tr>
        </tfoot>
      </table>
    </div>
  );
}
