import { useCallback, useMemo, useState } from "react";

import { ATTENDANCE_IS_SAMPLE, listAttendance } from "@/api/client";
import type { AttendanceRecord, AttendanceStatus } from "@/api/types";
import { departmentLabel } from "@/data/departments";
import { useAsync } from "@/lib/useAsync";
import { cn } from "@/lib/cn";
import { formatMinutes, formatTime } from "@/lib/format";
import {
  AttendancePill,
  Avatar,
  Card,
  DataTable,
  EmptyState,
  ErrorState,
  PageHeader,
  SampleChip,
  SectionHeader,
  Skeleton,
} from "@/ui";
import type { Column } from "@/ui";
import { CalendarCheckIcon, ClockIcon, MapPinIcon } from "@/ui/icons";

const TODAY_ISO = "2026-08-24";

function toISO(d: Date): string {
  return d.toISOString().slice(0, 10);
}

/** Monday-based week containing `iso`. */
function weekOf(iso: string): { iso: string; weekday: string; day: number }[] {
  const base = new Date(`${iso}T00:00:00Z`);
  const dow = (base.getUTCDay() + 6) % 7; // 0 = Monday
  const monday = new Date(base);
  monday.setUTCDate(base.getUTCDate() - dow);
  return Array.from({ length: 7 }, (_, i) => {
    const d = new Date(monday);
    d.setUTCDate(monday.getUTCDate() + i);
    return {
      iso: toISO(d),
      weekday: d.toLocaleDateString("en-IN", { weekday: "short", timeZone: "UTC" }),
      day: d.getUTCDate(),
    };
  });
}

const SOURCE_LABEL: Record<AttendanceRecord["source"], string> = {
  web: "Web",
  mobile: "Mobile",
  biometric: "Biometric",
  field: "Field",
};

const columns: Column<AttendanceRecord>[] = [
  {
    key: "name",
    header: "Employee",
    render: (r) => (
      <div className="flex items-center gap-3">
        <Avatar name={r.full_name} size="sm" />
        <div className="flex flex-col leading-tight">
          <span className="font-semibold text-ink">{r.full_name}</span>
          <span className="text-xs text-muted">{departmentLabel(r)}</span>
        </div>
      </div>
    ),
  },
  {
    key: "check_in",
    header: "Check-in",
    render: (r) => <span className="tnum text-ink-soft">{formatTime(r.check_in)}</span>,
  },
  {
    key: "check_out",
    header: "Check-out",
    render: (r) => <span className="tnum text-ink-soft">{formatTime(r.check_out)}</span>,
  },
  {
    key: "hours",
    header: "Hours",
    cellClassName: "hidden sm:table-cell",
    headerClassName: "hidden sm:table-cell",
    render: (r) => <span className="tnum text-ink-soft">{formatMinutes(r.worked_minutes)}</span>,
  },
  {
    key: "source",
    header: "Source",
    cellClassName: "hidden md:table-cell",
    headerClassName: "hidden md:table-cell",
    render: (r) => <span className="text-muted">{SOURCE_LABEL[r.source]}</span>,
  },
  {
    key: "status",
    header: "Status",
    align: "right",
    render: (r) => <AttendancePill status={r.status} />,
  },
];

function SummaryTile({
  label,
  count,
  status,
}: {
  label: string;
  count: number;
  status: AttendanceStatus;
}) {
  const tone =
    status === "present"
      ? "text-present"
      : status === "late"
        ? "text-late"
        : "text-danger";
  return (
    <Card className="p-4 flex items-center justify-between">
      <div className="flex flex-col">
        <span className={cn("text-2xl font-extrabold tnum", tone)}>{count}</span>
        <span className="text-xs font-medium text-muted">{label}</span>
      </div>
      <span className={cn("size-2.5 rounded-full", `bg-current ${tone}`)} aria-hidden />
    </Card>
  );
}

export function AttendancePage() {
  const [date, setDate] = useState(TODAY_ISO);
  const week = useMemo(() => weekOf(date), [date]);

  const loader = useCallback(() => listAttendance(date), [date]);
  const attendance = useAsync(loader, [date]);

  const records = attendance.status === "success" ? attendance.data : [];
  const present = records.filter((r) => r.status === "present").length;
  const late = records.filter((r) => r.status === "late").length;
  const absent = records.filter((r) => r.status === "absent").length;
  // Actual field check-ins only — a field-mode worker who is absent has no punch to show.
  const fieldPunches = records.filter(
    (r) => r.source === "field" && r.check_in !== null,
  );

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Daily punches"
        title="Attendance"
        actions={
          <>
            {ATTENDANCE_IS_SAMPLE ? <SampleChip /> : null}
            <label className="rounded-full bg-surface shadow-card px-4 h-11 inline-flex items-center gap-2 text-sm font-semibold cursor-pointer">
            <CalendarCheckIcon className="size-4 text-muted" />
            <span className="sr-only">Select date</span>
            <input
              type="date"
              value={date}
              onChange={(e) => setDate(e.target.value || TODAY_ISO)}
              className="bg-transparent text-ink focus:outline-none tnum"
            />
            </label>
          </>
        }
      />

      {/* Week strip */}
      <Card className="p-3">
        <div className="flex items-center gap-1.5 overflow-x-auto">
          {week.map((d) => {
            const active = d.iso === date;
            return (
              <button
                key={d.iso}
                type="button"
                onClick={() => setDate(d.iso)}
                aria-pressed={active}
                className={cn(
                  "flex flex-1 min-w-14 flex-col items-center gap-0.5 rounded-tile px-3 py-2.5 transition-colors",
                  active ? "bg-accent text-white" : "hover:bg-ground text-ink-soft",
                )}
              >
                <span className="text-xs font-medium opacity-70">{d.weekday}</span>
                <span className="text-lg font-bold tnum">{d.day}</span>
              </button>
            );
          })}
        </div>
      </Card>

      {/* Summary */}
      <section aria-label="Attendance summary" className="grid grid-cols-3 gap-4">
        {attendance.status === "success" ? (
          <>
            <SummaryTile label="Present" count={present + late} status="present" />
            <SummaryTile label="Late arrivals" count={late} status="late" />
            <SummaryTile label="Absent" count={absent} status="absent" />
          </>
        ) : (
          Array.from({ length: 3 }).map((_, i) => (
            <Card key={i} className="p-4">
              <Skeleton className="h-8 w-16" />
            </Card>
          ))
        )}
      </section>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 items-start">
        {/* Punch table */}
        <Card className="lg:col-span-2 p-6">
          <SectionHeader title="Punches" subtitle={`${records.length} records`} />
          <div className="mt-3">
            {attendance.status === "loading" ? (
              <div className="flex flex-col gap-3 py-2">
                {Array.from({ length: 8 }).map((_, i) => (
                  <Skeleton key={i} className="h-12 w-full rounded-tile" />
                ))}
              </div>
            ) : null}
            {attendance.status === "error" ? (
              <ErrorState message={attendance.error.message} />
            ) : null}
            {attendance.status === "success" ? (
              <DataTable
                caption="Attendance punches for the selected day"
                columns={columns}
                rows={records}
                rowKey={(r) => r.id}
                emptyState={
                  <EmptyState
                    icon={<ClockIcon className="size-6" />}
                    title="No punches for this day"
                    description="Nothing was recorded on this date. Pick another day from the week strip."
                  />
                }
              />
            ) : null}
          </div>
        </Card>

        {/* Field punches */}
        <Card className="p-6">
          <SectionHeader
            title="Field punches"
            subtitle="Checked in away from a desk"
          />
          <div className="mt-4 flex flex-col gap-2">
            {attendance.status === "success" ? (
              fieldPunches.length > 0 ? (
                fieldPunches.map((r) => (
                  <div
                    key={r.id}
                    className="flex items-center gap-3 rounded-tile bg-ground/60 px-3 py-2.5"
                  >
                    <Avatar name={r.full_name} size="sm" />
                    <div className="flex min-w-0 flex-col leading-tight">
                      <span className="truncate font-semibold text-ink text-sm">
                        {r.full_name}
                      </span>
                      <span className="inline-flex items-center gap-1 text-xs text-muted">
                        <MapPinIcon className="size-3.5" />
                        Field · {formatTime(r.check_in)}
                      </span>
                    </div>
                    <div className="ml-auto">
                      <AttendancePill status={r.status} />
                    </div>
                  </div>
                ))
              ) : (
                <EmptyState
                  title="No field punches"
                  description="Everyone on shift checked in from the office or remotely today."
                />
              )
            ) : (
              Array.from({ length: 3 }).map((_, i) => (
                <Skeleton key={i} className="h-14 w-full rounded-tile" />
              ))
            )}
          </div>
        </Card>
      </div>
    </div>
  );
}
