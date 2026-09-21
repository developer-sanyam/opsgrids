import { useCallback, useState } from "react";

import { TODAY, WEEK_START, getPeople, getProjects, listTimesheets, setTimesheetStatus } from "@/api/client";
import { addWeeks, weekFrom } from "@/api/dates";
import type { Person, Project, TimesheetRow, TimesheetStatus } from "@/api/types";
import { teamUtilization, weeklyBillableHours, weeklyEnteredHours } from "@/data/derive";
import { formatHours, formatWeekRange } from "@/lib/format";
import { Can, problemMessage, useAsync, useCan, useMutation } from "@opsgrids/client";
import {
  ArrowLeftIcon,
  ArrowRightIcon,
  Button,
  Card,
  ClockIcon,
  EmptyState,
  ErrorState,
  PageHeader,
  Pill,
  SectionHeader,
  Skeleton,
  useToast,
} from "@opsgrids/ui";
import { TimeGrid } from "@/ui";
import { TimesheetEntryDialog } from "./TimesheetEntryDialog";

interface TimesheetData {
  rows: TimesheetRow[];
  people: Person[];
  projects: Project[];
}

const STATUS_FILTERS: { value: TimesheetStatus | "all"; label: string }[] = [
  { value: "all", label: "All" },
  { value: "draft", label: "Draft" },
  { value: "submitted", label: "Submitted" },
  { value: "approved", label: "Approved" },
];

function SummaryTile({ label, value, sub }: { label: string; value: string; sub: string }) {
  return (
    <Card className="p-5 flex flex-col gap-1">
      <span className="text-sm font-medium text-muted">{label}</span>
      <span className="display text-2xl font-extrabold tnum leading-none">{value}</span>
      <span className="text-xs text-faint">{sub}</span>
    </Card>
  );
}

export function TimesheetsPage() {
  const toast = useToast();
  const canWrite = useCan("pulseboard.time.timesheet.write");
  const [week, setWeek] = useState(WEEK_START);
  const [statusFilter, setStatusFilter] = useState<TimesheetStatus | "all">("all");
  const [busyPersonId, setBusyPersonId] = useState<string | null>(null);
  const [editing, setEditing] = useState<{ row: TimesheetRow | null; person: Person } | null>(null);

  const weekDates = weekFrom(week);

  const loadTimesheets = useCallback(async (): Promise<TimesheetData> => {
    const [page, people, projects] = await Promise.all([
      listTimesheets({ week, status: statusFilter === "all" ? undefined : statusFilter }),
      getPeople(),
      getProjects(),
    ]);
    return { rows: page.items, people, projects };
  }, [week, statusFilter]);

  const state = useAsync(loadTimesheets, [week, statusFilter]);

  const advance = useMutation(
    ({ row, target }: { row: TimesheetRow; target: TimesheetStatus }) =>
      setTimesheetStatus({ person_ref: row.person_id, week_start: row.week_start, status: target }),
    {
      onSuccess: () => {
        setBusyPersonId(null);
        state.reload();
      },
      onError: (error) => {
        setBusyPersonId(null);
        toast.error("That status change didn't go through", { description: problemMessage(error) });
      },
    },
  );

  function submit(row: TimesheetRow) {
    setBusyPersonId(row.person_id);
    void advance.mutate({ row, target: "submitted" });
  }
  function approve(row: TimesheetRow) {
    setBusyPersonId(row.person_id);
    void advance.mutate({ row, target: "approved" });
  }
  function reopen(row: TimesheetRow) {
    setBusyPersonId(row.person_id);
    void advance.mutate({ row, target: "draft" });
  }
  function onAdvanceClick(row: TimesheetRow) {
    if (row.status === "draft") submit(row);
    else approve(row);
  }

  const summary = state.status === "success" ? {
    entered: weeklyEnteredHours(state.data.rows),
    billable: weeklyBillableHours(state.data.rows),
    util: teamUtilization(state.data.rows, state.data.people),
    approved: state.data.rows.filter((r) => r.status === "approved").length,
    total: state.data.rows.length,
  } : null;

  const peopleWithoutRow =
    state.status === "success"
      ? state.data.people.filter((p) => !state.data.rows.some((r) => r.person_id === p.id))
      : [];

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Time & billing"
        title="Timesheets"
        actions={
          <div className="inline-flex items-center gap-2 rounded-full bg-surface shadow-card px-2 py-1">
            <Button variant="ghost" aria-label="Previous week" onClick={() => setWeek((w) => addWeeks(w, -1))}>
              <ArrowLeftIcon className="size-4" />
            </Button>
            <span className="tnum px-1 text-sm font-semibold">{formatWeekRange(weekDates)}</span>
            <Button variant="ghost" aria-label="Next week" onClick={() => setWeek((w) => addWeeks(w, 1))}>
              <ArrowRightIcon className="size-4" />
            </Button>
          </div>
        }
      />

      <div className="flex items-center gap-1.5 overflow-x-auto -mx-1 px-1">
        {STATUS_FILTERS.map((f) => (
          <Pill key={f.value} active={statusFilter === f.value} onClick={() => setStatusFilter(f.value)}>
            {f.label}
          </Pill>
        ))}
      </div>

      <section aria-label="Week summary" className="grid grid-cols-2 gap-4 xl:grid-cols-4">
        {summary ? (
          <>
            <SummaryTile label="Hours entered" value={formatHours(summary.entered)} sub="across the team" />
            <SummaryTile label="Billable hours" value={formatHours(summary.billable)} sub="charged to clients" />
            <SummaryTile label="Utilization" value={`${summary.util}%`} sub="billable of capacity" />
            <SummaryTile label="Approved" value={`${summary.approved}/${summary.total}`} sub="sheets signed off" />
          </>
        ) : (
          Array.from({ length: 4 }).map((_, i) => (
            <Card key={i} className="p-5 flex flex-col gap-2">
              <Skeleton className="h-4 w-24" />
              <Skeleton className="h-7 w-16" />
            </Card>
          ))
        )}
      </section>

      <Card className="p-4 sm:p-6 flex flex-col gap-4">
        <SectionHeader
          title="Weekly time grid"
          subtitle="Entered hours with the billable share, per person per day"
          action={
            state.status === "success" && canWrite && peopleWithoutRow.length > 0 ? (
              <Button
                variant="outline"
                size="sm"
                onClick={() => peopleWithoutRow[0] && setEditing({ row: null, person: peopleWithoutRow[0] })}
              >
                Add a person&apos;s week
              </Button>
            ) : null
          }
        />
        {state.status === "loading" ? (
          <div className="flex flex-col gap-3 py-2">
            {Array.from({ length: 8 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full rounded-tile" />
            ))}
          </div>
        ) : null}
        {state.status === "error" ? (
          <ErrorState
            title="Couldn't load timesheets"
            message={problemMessage(state.error)}
            action={<Button onClick={state.reload}>Try again</Button>}
          />
        ) : null}
        {state.status === "success" ? (
          state.data.rows.length === 0 ? (
            <EmptyState
              icon={<ClockIcon className="size-6" />}
              title={statusFilter === "all" ? "No time recorded for this week" : `No ${statusFilter} sheets this week`}
              description={
                canWrite
                  ? "Record a week for someone on the roster to get started."
                  : "Nobody has logged time for this week yet."
              }
              action={
                canWrite && peopleWithoutRow.length > 0 ? (
                  <Can permission="pulseboard.time.timesheet.write">
                    <Button onClick={() => peopleWithoutRow[0] && setEditing({ row: null, person: peopleWithoutRow[0] })}>
                      Record a week
                    </Button>
                  </Can>
                ) : null
              }
            />
          ) : (
            <TimeGrid
              rows={state.data.rows}
              people={state.data.people}
              weekDates={weekDates}
              today={TODAY}
              canWrite={canWrite}
              busyPersonId={busyPersonId}
              onAdvance={onAdvanceClick}
              onReopen={reopen}
              onEditWeek={(row) => {
                const person = state.data.people.find((p) => p.id === row.person_id);
                if (person) setEditing({ row, person });
              }}
            />
          )
        ) : null}
      </Card>

      {editing ? (
        <TimesheetEntryDialog
          row={editing.row}
          person={editing.person}
          projects={state.status === "success" ? state.data.projects : []}
          weekDates={weekDates}
          onClose={() => setEditing(null)}
          onSaved={(_row, message) => {
            toast.success(message);
            state.reload();
          }}
        />
      ) : null}
    </div>
  );
}
