import { useCallback, useMemo } from "react";

import { getPeople, getTimesheets, WEEK_DATES, TODAY } from "@/api/client";
import type { Person, TimesheetRow } from "@/api/types";
import {
  teamUtilization,
  weeklyBillableHours,
  weeklyEnteredHours,
} from "@/data/derive";
import { formatHours } from "@/lib/format";
import { useAsync } from "@atmora/client";
import { Card, ErrorState, PageHeader, SectionHeader, Skeleton, TimeGrid } from "@/ui";
import { CalendarIcon, ClockIcon } from "@atmora/ui";

interface TimesheetData {
  rows: TimesheetRow[];
  people: Person[];
}

async function loadTimesheets(): Promise<TimesheetData> {
  const [rows, people] = await Promise.all([getTimesheets(), getPeople()]);
  return { rows, people };
}

function SummaryTile({
  label,
  value,
  sub,
}: {
  label: string;
  value: string;
  sub: string;
}) {
  return (
    <Card className="p-5 flex flex-col gap-1">
      <span className="text-sm font-medium text-muted">{label}</span>
      <span className="display text-2xl font-extrabold tnum leading-none">{value}</span>
      <span className="text-xs text-faint">{sub}</span>
    </Card>
  );
}

export function TimesheetsPage() {
  const state = useAsync(useCallback(loadTimesheets, []), []);

  const summary = useMemo(() => {
    if (state.status !== "success") return null;
    const entered = weeklyEnteredHours(state.data.rows);
    const billable = weeklyBillableHours(state.data.rows);
    const util = teamUtilization(state.data.rows, state.data.people);
    const approved = state.data.rows.filter((r) => r.status === "approved").length;
    return { entered, billable, util, approved, total: state.data.rows.length };
  }, [state]);

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Time & billing"
        title="Timesheets"
        actions={
          <span className="rounded-full bg-surface shadow-card px-4 h-11 inline-flex items-center gap-2 text-sm font-semibold">
            <CalendarIcon className="size-4 text-muted" />
            <span className="tnum">Week of 24 Aug 2026</span>
          </span>
        }
      />

      <section aria-label="Week summary" className="grid grid-cols-2 gap-4 xl:grid-cols-4">
        {summary ? (
          <>
            <SummaryTile label="Hours entered" value={formatHours(summary.entered)} sub="across the team" />
            <SummaryTile label="Billable hours" value={formatHours(summary.billable)} sub="charged to clients" />
            <SummaryTile label="Utilization" value={`${summary.util}%`} sub="billable of capacity" />
            <SummaryTile
              label="Approved"
              value={`${summary.approved}/${summary.total}`}
              sub="sheets signed off"
            />
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
            <span className="hidden items-center gap-1.5 text-xs font-medium text-muted sm:inline-flex">
              <ClockIcon className="size-4" />
              <span className="text-accent font-semibold">bill</span> = billable
            </span>
          }
        />
        {state.status === "loading" ? (
          <div className="flex flex-col gap-3 py-2">
            {Array.from({ length: 8 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full rounded-tile" />
            ))}
          </div>
        ) : null}
        {state.status === "error" ? <ErrorState message={state.error.message} /> : null}
        {state.status === "success" ? (
          <TimeGrid rows={state.data.rows} people={state.data.people} weekDates={WEEK_DATES} today={TODAY} />
        ) : null}
      </Card>
    </div>
  );
}
