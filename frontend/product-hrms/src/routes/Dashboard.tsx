import { useCallback } from "react";
import { Link } from "react-router-dom";

import {
  ATTENDANCE_IS_SAMPLE,
  getDashboardStats,
  getRecentJoiners,
  listAttendance,
} from "@/api/client";
import type { StatCardData } from "@/api/types";
import { useAuth } from "@/auth/context";
import { departmentLabel, departmentName } from "@/data/departments";
import { useAsync } from "@/lib/useAsync";
import { formatDate, formatTime } from "@/lib/format";
import {
  AssistantPanel,
  AttendanceBarChart,
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
  StatCard,
} from "@/ui";
import type { Column } from "@/ui";
import type { AttendanceRecord } from "@/api/types";
import {
  CalendarCheckIcon,
  ClipboardCheckIcon,
  LeafIcon,
  UsersIcon,
} from "@/ui/icons";

const TODAY = "2026-08-24";

const STAT_ICONS: Record<StatCardData["key"], React.ReactNode> = {
  total_employees: <UsersIcon className="size-5" />,
  present_today: <CalendarCheckIcon className="size-5" />,
  on_leave: <LeafIcon className="size-5" />,
  pending_approvals: <ClipboardCheckIcon className="size-5" />,
};

const attendanceColumns: Column<AttendanceRecord>[] = [
  {
    key: "name",
    header: "Employee",
    render: (r) => (
      <div className="flex items-center gap-3">
        <Avatar name={r.full_name} size="sm" />
        <div className="flex flex-col leading-tight">
          <span className="font-semibold text-ink">{r.full_name}</span>
          <span className="text-xs text-muted">{r.employee_no}</span>
        </div>
      </div>
    ),
  },
  {
    key: "department",
    header: "Department",
    cellClassName: "hidden sm:table-cell text-muted",
    headerClassName: "hidden sm:table-cell",
    render: (r) => departmentName(r.department_id),
  },
  {
    key: "check_in",
    header: "Check-in",
    render: (r) => <span className="tnum text-ink-soft">{formatTime(r.check_in)}</span>,
  },
  {
    key: "status",
    header: "Status",
    align: "right",
    render: (r) => <AttendancePill status={r.status} />,
  },
];

export function DashboardPage() {
  const { user } = useAuth();
  const stats = useAsync(getDashboardStats, []);
  const attendance = useAsync(useCallback(() => listAttendance(TODAY), []), []);
  const joiners = useAsync(getRecentJoiners, []);

  const greetingName = user?.full_name.split(" ")[0] ?? "there";

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Ready to make today count?"
        title={
          <>
            Welcome back, {greetingName}{" "}
            <span aria-hidden>👋</span>
          </>
        }
      />

      {/* Stat cards */}
      <section aria-label="Key metrics" className="group grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
        {stats.status === "loading"
          ? Array.from({ length: 4 }).map((_, i) => (
              <Card key={i} className="p-5 flex flex-col gap-4">
                <Skeleton className="h-4 w-28" />
                <Skeleton className="h-10 w-20 rounded-tile" />
              </Card>
            ))
          : null}
        {stats.status === "error" ? (
          <Card className="p-5 sm:col-span-2 xl:col-span-4">
            <ErrorState message={stats.error.message} />
          </Card>
        ) : null}
        {stats.status === "success"
          ? stats.data.cards.map((card) => (
              <StatCard
                key={card.key}
                data={card}
                icon={STAT_ICONS[card.key]}
                sample={ATTENDANCE_IS_SAMPLE && card.key !== "total_employees"}
              />
            ))
          : null}
      </section>

      {/* Main + rail */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 items-start">
        <div className="lg:col-span-2 flex flex-col gap-6">
          {/* Attendance overview */}
          <Card className="p-6">
            <SectionHeader
              title="Attendance Overview"
              subtitle="Present headcount this week"
              action={ATTENDANCE_IS_SAMPLE ? <SampleChip /> : undefined}
            />
            {stats.status === "success" ? (
              <AttendanceBarChart
                data={stats.data.week}
                max={stats.data.cards[0]?.value ?? 24}
              />
            ) : (
              <Skeleton className="h-44 w-full rounded-tile mt-4" />
            )}
          </Card>

          {/* Today's attendance */}
          <Card className="p-6">
            <SectionHeader
              title="Today's Attendance"
              subtitle={formatDate(TODAY)}
              action={
                <div className="flex items-center gap-2">
                  {ATTENDANCE_IS_SAMPLE ? <SampleChip /> : null}
                  <Link
                    to="/attendance"
                    className="rounded-full bg-ground px-4 h-9 inline-flex items-center text-sm font-semibold text-ink hover:bg-line transition-colors"
                  >
                    View all
                  </Link>
                </div>
              }
            />
            <div className="mt-3">
              {attendance.status === "loading" ? (
                <div className="flex flex-col gap-3 py-2">
                  {Array.from({ length: 5 }).map((_, i) => (
                    <Skeleton key={i} className="h-12 w-full rounded-tile" />
                  ))}
                </div>
              ) : null}
              {attendance.status === "error" ? (
                <ErrorState message={attendance.error.message} />
              ) : null}
              {attendance.status === "success" ? (
                <DataTable
                  caption="Today's attendance punches"
                  columns={attendanceColumns}
                  rows={attendance.data.slice(0, 6)}
                  rowKey={(r) => r.id}
                  emptyState={
                    <EmptyState
                      title="No punches yet"
                      description="Attendance for today will appear here as people check in."
                    />
                  }
                />
              ) : null}
            </div>
          </Card>
        </div>

        {/* Right rail */}
        <div className="flex flex-col gap-6">
          <AssistantPanel name={greetingName} />

          <Card className="p-6">
            <SectionHeader title="Recent Joiners" subtitle="Newest members of the team" />
            <div className="mt-4 flex flex-col gap-1">
              {joiners.status === "loading"
                ? Array.from({ length: 4 }).map((_, i) => (
                    <Skeleton key={i} className="h-14 w-full rounded-tile" />
                  ))
                : null}
              {joiners.status === "error" ? (
                <ErrorState message={joiners.error.message} />
              ) : null}
              {joiners.status === "success"
                ? joiners.data.map((emp) => (
                    <div
                      key={emp.id}
                      className="flex items-center gap-3 rounded-tile px-2 py-2 hover:bg-ground transition-colors"
                    >
                      <Avatar name={emp.full_name} />
                      <div className="flex min-w-0 flex-col leading-tight">
                        <span className="truncate font-semibold text-ink">
                          {emp.full_name}
                        </span>
                        <span className="truncate text-xs text-muted">
                          {emp.designation} · {departmentLabel(emp)}
                        </span>
                      </div>
                      <span className="ml-auto shrink-0 text-xs font-medium text-faint tnum">
                        {formatDate(emp.date_of_joining)}
                      </span>
                    </div>
                  ))
                : null}
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}
