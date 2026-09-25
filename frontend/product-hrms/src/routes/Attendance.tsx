import { useCallback, useEffect, useMemo, useState } from "react";

import { USE_FIXTURES, checkOut, listAttendance, listAttendanceRoster } from "@/api/client";
import type { AttendanceRecord, AttendanceStatus } from "@/api/types";
import { departmentLabel } from "@/data/departments";
import { getMyEmployeeId } from "@/lib/subject";
import { useZoneLookup } from "@/lib/zone";
import { ApiError, problemMessage, useAsync, useCan } from "@opsgrids/client";
import {
  Announcer,
  Button,
  CalendarCheckIcon,
  ClockIcon,
  MapPinIcon,
  Pagination,
  RowActions,
  cn,
  formatInstant,
  useAnnounce,
  useToast,
} from "@opsgrids/ui";
import type { ZoneLookup } from "@opsgrids/ui";
import { formatMinutes } from "@/lib/format";
import {
  Avatar,
  Card,
  DataTable,
  EmptyState,
  ErrorState,
  PageHeader,
  SectionHeader,
  Skeleton,
} from "@/ui";
import type { Column } from "@/ui";
import { AttendancePill } from "@/ui/StatusPill";
import { CheckInDialog } from "@/ui/CheckInDialog";
import { CorrectAttendanceDialog } from "@/ui/CorrectAttendanceDialog";
import { CorrectionCell } from "@/ui/CorrectionCell";
import { RequestCorrectionDialog } from "@/ui/RequestCorrectionDialog";

/** The real clock, not a frozen fixture date — injectable so a test can pin it. */
function todayISO(): string {
  return new Date().toISOString().slice(0, 10);
}

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
};

/** A punch instant, in the record's own location zone (ADR-0000, C9) — "—" for a missing punch,
 *  a plain ellipsis while the zone lookup is still loading (never the browser's zone as a
 *  stand-in). */
function punchTime(
  iso: string | null,
  record: AttendanceRecord,
  zoneLookup: ZoneLookup | null,
): string {
  if (!iso) return "—";
  if (!zoneLookup) return "…";
  return formatInstant(iso, record.location_code, zoneLookup, "time").text;
}

type MyPunchStatus =
  | { kind: "loading" }
  | { kind: "no-employee" }
  | { kind: "fixture-unsupported" }
  | { kind: "not-checked-in" }
  | { kind: "checked-in"; recordId: string }
  | { kind: "checked-out" }
  | { kind: "error"; message: string };

function useMyPunchStatus(date: string, refreshKey: number): MyPunchStatus {
  const [status, setStatus] = useState<MyPunchStatus>({ kind: "loading" });
  const myEmployeeId = getMyEmployeeId();

  useEffect(() => {
    // The punch roster is real, but check-in/out has no fixture-mode stand-in yet (there is no
    // offline session to punch against) — disable rather than attempt a live call that will fail.
    if (USE_FIXTURES) {
      setStatus({ kind: "fixture-unsupported" });
      return;
    }
    if (!myEmployeeId) {
      setStatus({ kind: "no-employee" });
      return;
    }
    let active = true;
    setStatus({ kind: "loading" });
    listAttendance({ employeeId: myEmployeeId, dateFrom: date, dateTo: date, size: 1 })
      .then((page) => {
        if (!active) return;
        const record = page.items[0];
        if (!record) {
          setStatus({ kind: "not-checked-in" });
        } else if (record.check_out_at) {
          setStatus({ kind: "checked-out" });
        } else {
          setStatus({ kind: "checked-in", recordId: record.id });
        }
      })
      .catch((err: unknown) => {
        if (active) {
          setStatus({ kind: "error", message: err instanceof Error ? err.message : "Something went wrong" });
        }
      });
    return () => {
      active = false;
    };
  }, [myEmployeeId, date, refreshKey]);

  return status;
}

const PAGE_SIZE = 25;

function AttendanceTable() {
  const [date, setDate] = useState(todayISO());
  const [page, setPage] = useState(1);
  const [refreshKey, setRefreshKey] = useState(0);
  const [correctionRecord, setCorrectionRecord] = useState<AttendanceRecord | null>(null);
  const [correctingHr, setCorrectingHr] = useState<AttendanceRecord | null>(null);
  const [checkingIn, setCheckingIn] = useState(false);
  const week = useMemo(() => weekOf(date), [date]);
  const zoneLookup = useZoneLookup();
  const announce = useAnnounce();
  const toast = useToast();
  const myEmployeeId = getMyEmployeeId();
  const canHrCorrect = useCan("hrms.time.attendance.write", "ORG");

  const punchStatus = useMyPunchStatus(date, refreshKey);

  const loader = useCallback(() => listAttendanceRoster(date, { page, size: PAGE_SIZE }), [date, page]);
  const attendance = useAsync(loader, [date, page, refreshKey]);

  function reload() {
    setRefreshKey((k) => k + 1);
  }

  async function doCheckOut() {
    try {
      await checkOut({});
      toast.success("Checked out.");
      reload();
    } catch (err) {
      toast.error(problemMessage(err));
    }
  }

  const records = attendance.status === "success" ? attendance.data.items : [];
  const total = attendance.status === "success" ? attendance.data.total : 0;
  const present = records.filter((r) => r.status === "present").length;
  const halfDay = records.filter((r) => r.status === "half_day").length;
  const absent = records.filter((r) => r.status === "absent").length;
  // Real field check-ins: `work_mode === "field"`, never `source` — a field punch's source is
  // still web/mobile/biometric (the wire's `Source` enum has no `"field"` member).
  const fieldPunches = records.filter((r) => r.work_mode === "field" && r.check_in !== null);

  const columns: Column<AttendanceRecord>[] = useMemo(
    () => [
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
        render: (r) => (
          <span className="tnum text-ink-soft">{punchTime(r.check_in, r, zoneLookup)}</span>
        ),
      },
      {
        key: "check_out",
        header: "Check-out",
        render: (r) => (
          <span className="tnum text-ink-soft">{punchTime(r.check_out, r, zoneLookup)}</span>
        ),
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
        render: (r) => (
          <div className="flex flex-col items-end gap-1.5">
            <AttendancePill status={r.status} />
            <div className="md:hidden">
              <CorrectionCell
                record={r}
                isOwn={r.employee_id === myEmployeeId}
                zoneLookup={zoneLookup}
                onRequestCorrection={setCorrectionRecord}
              />
            </div>
          </div>
        ),
      },
      {
        key: "correction",
        header: "Correction",
        align: "right",
        cellClassName: "hidden md:table-cell",
        headerClassName: "hidden md:table-cell",
        render: (r) => (
          <CorrectionCell
            record={r}
            isOwn={r.employee_id === myEmployeeId}
            zoneLookup={zoneLookup}
            onRequestCorrection={setCorrectionRecord}
          />
        ),
      },
      ...(canHrCorrect
        ? [
            {
              key: "actions",
              header: "",
              align: "right" as const,
              render: (r: AttendanceRecord) => (
                <RowActions
                  label={r.full_name}
                  actions={[
                    {
                      label: "Correct status",
                      onSelect: () => setCorrectingHr(r),
                      disabled: r.status === "regularized" || r.allowed_transitions.length === 0,
                      disabledReason: "This record is regularised and can no longer be corrected.",
                    },
                  ]}
                />
              ),
            },
          ]
        : []),
    ],
    [myEmployeeId, zoneLookup, canHrCorrect],
  );

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Daily punches"
        title="Attendance"
        actions={
          <>
            {punchStatus.kind === "checked-in" ? (
              <Button variant="dark" onClick={() => void doCheckOut()}>
                Check out
              </Button>
            ) : (
              <Button
                variant="dark"
                onClick={() => setCheckingIn(true)}
                disabled={
                  punchStatus.kind === "no-employee" ||
                  punchStatus.kind === "checked-out" ||
                  punchStatus.kind === "fixture-unsupported"
                }
                title={
                  punchStatus.kind === "no-employee"
                    ? "No employee record is linked to your account."
                    : punchStatus.kind === "checked-out"
                      ? "Already checked out for the day."
                      : punchStatus.kind === "fixture-unsupported"
                        ? "Not available in fixture mode."
                        : undefined
                }
              >
                Check in
              </Button>
            )}
            <label className="rounded-full bg-surface shadow-card px-4 h-11 inline-flex items-center gap-2 text-sm font-semibold cursor-pointer focus-within:outline focus-within:outline-2 focus-within:outline-accent focus-within:outline-offset-2">
              <CalendarCheckIcon className="size-4 text-muted" />
              <span className="sr-only">Select date</span>
              <input
                type="date"
                value={date}
                onChange={(e) => {
                  setDate(e.target.value || todayISO());
                  setPage(1);
                }}
                className="bg-transparent text-ink focus:outline-none tnum"
              />
            </label>
          </>
        }
      />

      {punchStatus.kind === "no-employee" ? (
        <Card className="p-4">
          <p className="text-sm text-muted">
            Your account has no employee record linked, so you can't punch attendance here.
          </p>
        </Card>
      ) : null}

      {/* Week strip */}
      <Card className="p-3">
        <div className="flex items-center gap-1.5 overflow-x-auto">
          {week.map((d) => {
            const active = d.iso === date;
            return (
              <button
                key={d.iso}
                type="button"
                onClick={() => {
                  setDate(d.iso);
                  setPage(1);
                }}
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
            <SummaryTile label="Present" count={present} status="present" />
            <SummaryTile label="Half day" count={halfDay} status="half_day" />
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
          <SectionHeader title="Punches" subtitle={`${total} records`} />
          <div className="mt-3">
            {attendance.status === "loading" ? (
              <div className="flex flex-col gap-3 py-2">
                {Array.from({ length: 8 }).map((_, i) => (
                  <Skeleton key={i} className="h-12 w-full rounded-tile" />
                ))}
              </div>
            ) : null}
            {attendance.status === "error" ? (
              attendance.error instanceof ApiError && attendance.error.status === 403 ? (
                <ErrorState
                  title="You can't see this roster"
                  message={problemMessage(attendance.error)}
                  tone="warning"
                />
              ) : (
                <ErrorState
                  title="Couldn't load attendance"
                  message={problemMessage(attendance.error)}
                  onRetry={() => attendance.reload()}
                />
              )
            ) : null}
            {attendance.status === "success" ? (
              <>
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
                {records.length > 0 ? (
                  <Pagination
                    page={attendance.data.page}
                    pages={attendance.data.pages}
                    total={attendance.data.total}
                    size={attendance.data.size}
                    noun="punches"
                    onPageChange={setPage}
                  />
                ) : null}
              </>
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
                        Field · {punchTime(r.check_in, r, zoneLookup)}
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

      {correctionRecord ? (
        <RequestCorrectionDialog
          open={correctionRecord !== null}
          onClose={() => setCorrectionRecord(null)}
          record={correctionRecord}
          zoneLookup={zoneLookup}
          onSettled={(result) => {
            setCorrectionRecord(null);
            reload();
            if (result.kind === "gone") {
              announce("That attendance record isn't available any more.");
            }
          }}
        />
      ) : null}

      {correctingHr ? (
        <CorrectAttendanceDialog
          record={correctingHr}
          onClose={() => setCorrectingHr(null)}
          onSaved={() => {
            toast.success(`${correctingHr.full_name}'s status corrected.`);
            setCorrectingHr(null);
            reload();
          }}
        />
      ) : null}

      {checkingIn ? (
        <CheckInDialog
          onClose={() => setCheckingIn(false)}
          onSaved={() => {
            toast.success("Checked in.");
            setCheckingIn(false);
            reload();
          }}
        />
      ) : null}
    </div>
  );
}

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
      : status === "half_day"
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
  return (
    <Announcer>
      <AttendanceTable />
    </Announcer>
  );
}
