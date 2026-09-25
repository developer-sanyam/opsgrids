/**
 * "My Workspace" — the Employee Home (`/`; mock-5's "Employee View"), rendered by `HomePage`
 * (`routes/Home.tsx`) instead of the Owner/Executive body once `useHomePersona()` (`./persona.ts`)
 * places the caller there.
 *
 * Every widget resolves the same `resolveAccess` gate the Owner Home's own widgets do —
 * entitlement first, then the grant the read needs, never fetching what a check has already
 * refused (`./access.ts`). HRMS's two self-service widgets (dashboard audit §5.9) additionally
 * need the caller's own employee id, read the same way `product-hrms/src/lib/subject.ts` reads it
 * — the access token's `sref` claim, a hint for the query and never an authorization decision;
 * the server re-checks every OWN-scoped read against it regardless (ADR-0003 §3). A membership
 * with no employee link (`employeeId === null`) renders neither widget at all, per that audit's
 * §2.6 deferral, rather than a notice about a link nothing here can create.
 *
 * "Company policies" — mock-5's fourth quick action — has no source anywhere in the platform (no
 * policy or document library, dashboard audit §3.7 row 11) and is left out rather than linked to a
 * page that 404s.
 */
import type { ReactNode } from "react";
import { useNavigate } from "react-router-dom";

import {
  decodeAccessToken,
  getPlatformHost,
  problemMessage,
  useAsync,
  useAuth,
  useCan,
  useEntitlement,
  usePermissions,
} from "@opsgrids/client";
import {
  Button,
  CalendarCheckIcon,
  Card,
  ClockIcon,
  EmptyState,
  ErrorState,
  InlineAlert,
  PageHeader,
  SectionHeader,
  Skeleton,
  SquareIcon,
  StatusBadge,
  UserIcon,
} from "@opsgrids/ui";

import { getMyAttendance, listMyLeaveBalances, listMyTasks } from "@/api";
import type { AttendanceRecord, LeaveBalance, Page, ProjectTask } from "@/api/types";
import { formatDate } from "@/lib/money";
import { resolveAccess } from "@/ui/home/access";
import type { AccessState } from "@/ui/home/access";
import { NoAccessNotice, PlanGatedNotice } from "@/ui/home/WidgetStates";

const SHORT_WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const SHORT_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** `1 September 2026, 09:14` → its first word — duplicated in miniature from `routes/Home.tsx`'s
 * own private helper of the same name: that file is mid-edit on its layout elsewhere in this
 * slice, and a five-line pure function is cheaper to keep in step by hand than to add a shared
 * import neither page otherwise needs. */
function firstNameOf(fullName: string): string {
  const first = fullName.trim().split(/\s+/)[0];
  return first && first.length > 0 ? first : "there";
}

/** Local time, the same reason `routes/Home.tsx`'s own `greetingFor` reads it: a greeting is not
 * a figure the organisation's own timezone owns. */
function greetingFor(hour: number): string {
  if (hour < 12) return "Good morning";
  if (hour < 17) return "Good afternoon";
  return "Good evening";
}

/** "Wed, 24 Sep" — mock-5's own date chip, read from local time for the same reason the greeting
 * is. Spelled out against a literal month list rather than `Intl`'s `short` style, which renders
 * September as "Sept" in some English locales (`lib/money.ts`'s `formatDate` fixes the same
 * thing the same way). */
function dayChip(date: Date): string {
  return `${SHORT_WEEKDAYS[date.getDay()]}, ${date.getDate()} ${SHORT_MONTHS[date.getMonth()]}`;
}

/** `yyyy-mm-dd` in the viewer's own local calendar — never `toISOString()`, which is UTC and
 * would name yesterday's or tomorrow's work day for anyone near midnight on either side of it. */
function isoDate(date: Date): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function formatTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("en-IN", { hour: "numeric", minute: "2-digit" }).format(date);
}

function byDueDateSoonestFirst(a: ProjectTask, b: ProjectTask): number {
  if (a.due_date === null && b.due_date === null) return 0;
  if (a.due_date === null) return 1;
  if (b.due_date === null) return -1;
  return a.due_date.localeCompare(b.due_date);
}

/** The shape every HRMS self-service card shares while its own grants are still loading — sized
 * like the card it stands in for, so nothing shifts once the answer lands. */
function CardSlotSkeleton({ label }: { label: string }) {
  return (
    <Card className="flex h-full flex-col gap-3 p-4 sm:p-6" role="status" aria-busy="true">
      <span className="sr-only">{`Loading ${label}`}</span>
      <Skeleton className="size-10 rounded-tile" />
      <Skeleton className="h-3 w-24 rounded-full" />
      <Skeleton className="h-6 w-32 rounded-tile" />
    </Card>
  );
}

function RetryCard({ label, onRetry }: { label: string; onRetry: () => void }) {
  return (
    <Card className="flex h-full flex-col justify-center gap-3 p-4 sm:p-6">
      <p className="text-sm font-semibold text-ink">{`Couldn't load ${label}.`}</p>
      <Button variant="secondary" size="sm" onClick={onRetry}>
        Try again
      </Button>
    </Card>
  );
}

interface QuickAction {
  key: string;
  label: string;
  href: string;
  icon: ReactNode;
}

export function EmployeeWorkspace() {
  const navigate = useNavigate();
  const { user } = useAuth();
  const { status: grantsStatus } = usePermissions();
  const employeeId = decodeAccessToken()?.sref ?? null;

  const hrmsEntitled = useEntitlement("hrms");
  const projectsEntitled = useEntitlement("projects");
  const canActivateProducts = useCan("core.product.activation.write", "ORG");

  const canReadTasks = useCan("projects.board.task.read");
  const canReadAttendance = useCan("hrms.time.attendance.read");
  const canReadLeaveBalance = useCan("hrms.time.leave_balance.read");
  const canWriteLeaveRequest = useCan("hrms.time.leave_request.write");
  const canWriteAttendance = useCan("hrms.time.attendance.write");
  const canReadEmployee = useCan("hrms.people.employee.read");

  const ready = grantsStatus === "ready";
  const goToProducts = () => navigate("/products");

  const tasksAccess: AccessState | null = ready ? resolveAccess(projectsEntitled, canReadTasks) : null;

  // The HRMS self-service cards render only for an entitled org with an employee link — per the
  // dashboard audit, neither is a "not active" or "no access" notice when it isn't, they simply
  // are not there. `null` here means "still finding out", `false` means "does not apply".
  const hrmsWidgetsApply = !ready ? null : hrmsEntitled && employeeId !== null;
  const attendanceAccess: AccessState | null = hrmsWidgetsApply === true ? resolveAccess(true, canReadAttendance) : null;
  const leaveAccess: AccessState | null = hrmsWidgetsApply === true ? resolveAccess(true, canReadLeaveBalance) : null;

  const today = new Date();
  const todayIso = isoDate(today);

  const tasks = useAsync<Page<ProjectTask> | null>(
    () => (tasksAccess === "ready" ? listMyTasks(50) : Promise.resolve(null)),
    [tasksAccess],
  );
  const attendance = useAsync<Page<AttendanceRecord> | null>(
    () =>
      attendanceAccess === "ready" && employeeId ? getMyAttendance(employeeId, todayIso) : Promise.resolve(null),
    [attendanceAccess, employeeId, todayIso],
  );
  const leaveBalances = useAsync<LeaveBalance[] | null>(
    () => (leaveAccess === "ready" && employeeId ? listMyLeaveBalances(employeeId) : Promise.resolve(null)),
    [leaveAccess, employeeId],
  );

  const firstName = firstNameOf(user?.full_name ?? "");
  const greeting = `${greetingFor(today.getHours())}, ${firstName}`;

  const hrmsHost = getPlatformHost("hrms");
  const projectsHost = getPlatformHost("projects");

  const quickActions: QuickAction[] = [];
  if (hrmsEntitled && canWriteLeaveRequest) {
    quickActions.push({
      key: "leave",
      label: "Apply Leave",
      href: `${hrmsHost}/leave`,
      icon: <CalendarCheckIcon className="size-5" />,
    });
  }
  if (hrmsEntitled && canWriteAttendance) {
    quickActions.push({
      key: "attendance",
      label: "Mark Attendance",
      href: `${hrmsHost}/attendance`,
      icon: <ClockIcon className="size-5" />,
    });
  }
  if (hrmsEntitled && canReadEmployee && employeeId) {
    quickActions.push({
      key: "profile",
      label: "My Profile",
      href: `${hrmsHost}/employees/${employeeId}`,
      icon: <UserIcon className="size-5" />,
    });
  }

  const pendingTasks =
    tasksAccess === "ready" && tasks.status === "success" && tasks.data
      ? [...tasks.data.items.filter((task) => task.stage !== "done")].sort(byDueDateSoonestFirst)
      : [];
  const upcomingTasks = pendingTasks.slice(0, 5);

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title={greeting}
        description="Here's your work for today."
        actions={
          <span className="inline-flex h-11 items-center gap-2 rounded-full bg-surface px-4 text-sm font-semibold text-ink-soft shadow-card">
            {dayChip(today)}
          </span>
        }
      />

      {hrmsWidgetsApply !== false ? (
        <section aria-label="Attendance and leave" className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          {hrmsWidgetsApply === null ? (
            <CardSlotSkeleton label="attendance today" />
          ) : attendanceAccess === "no_access" ? (
            <NoAccessNotice capability="your attendance" compact />
          ) : attendance.status === "loading" ? (
            <CardSlotSkeleton label="attendance today" />
          ) : attendance.status === "error" ? (
            <RetryCard label="attendance today" onRetry={attendance.reload} />
          ) : (
            <AttendanceTodayCard record={attendance.data?.items[0] ?? null} />
          )}

          {hrmsWidgetsApply === null ? (
            <CardSlotSkeleton label="leave balance" />
          ) : leaveAccess === "no_access" ? (
            <NoAccessNotice capability="your leave balance" compact />
          ) : leaveBalances.status === "loading" ? (
            <CardSlotSkeleton label="leave balance" />
          ) : leaveBalances.status === "error" ? (
            <RetryCard label="your leave balance" onRetry={leaveBalances.reload} />
          ) : (
            <LeaveBalanceCard balances={leaveBalances.data ?? []} />
          )}
        </section>
      ) : null}

      <Card className="flex flex-col gap-4 p-4 sm:p-6">
        <SectionHeader
          title="My Tasks"
          action={
            tasksAccess === "ready" ? (
              <a
                href={`${projectsHost}/board`}
                className="text-sm font-semibold text-accent-ink transition-colors hover:text-accent"
              >
                View All
              </a>
            ) : undefined
          }
        />
        {tasksAccess === null || (tasksAccess === "ready" && tasks.status === "loading") ? (
          <div className="flex flex-col gap-2" role="status" aria-busy="true">
            <span className="sr-only">Loading tasks</span>
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className="h-12 w-full rounded-tile" />
            ))}
          </div>
        ) : tasksAccess === "plan_gated" ? (
          <PlanGatedNotice product="Projects" canActivate={canActivateProducts} onStartTrial={goToProducts} />
        ) : tasksAccess === "no_access" ? (
          <NoAccessNotice capability="your tasks" />
        ) : tasks.status === "error" ? (
          <ErrorState message={problemMessage(tasks.error)} onRetry={tasks.reload} />
        ) : upcomingTasks.length === 0 ? (
          <InlineAlert tone="success" title="You're all set!">
            No pending tasks for today.
          </InlineAlert>
        ) : (
          <ul className="flex list-none flex-col gap-1 p-0">
            {upcomingTasks.map((task) => (
              <li key={task.id} className="-mx-2 flex items-center gap-3 rounded-tile px-2 py-3">
                <SquareIcon aria-hidden className="size-4 shrink-0 text-faint" />
                <span className="min-w-0 flex-1 truncate text-sm font-medium text-ink-soft">{task.title}</span>
                <StatusBadge status={task.priority} />
                <span className="shrink-0 text-xs text-muted tnum">
                  {task.due_date ? formatDate(task.due_date) : "No due date"}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Card>

      {quickActions.length > 0 ? (
        <section aria-label="Quick actions" className="flex flex-col gap-3">
          <h2 className="text-base font-bold text-ink">Quick Actions</h2>
          <ul className="grid list-none grid-cols-2 gap-3 p-0 sm:grid-cols-3">
            {quickActions.map((action) => (
              <li key={action.key}>
                <a
                  href={action.href}
                  className="flex flex-col items-center gap-2 rounded-card bg-surface p-4 text-center shadow-card transition-colors hover:bg-ground focus-visible:bg-ground"
                >
                  <span aria-hidden className="grid size-10 place-items-center rounded-tile bg-accent-soft text-accent-ink">
                    {action.icon}
                  </span>
                  <span className="text-xs font-semibold text-ink-soft">{action.label}</span>
                </a>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}

function AttendanceTodayCard({ record }: { record: AttendanceRecord | null }) {
  return (
    <Card className="flex h-full flex-col gap-3 p-4 sm:p-6">
      <span aria-hidden className="grid size-10 place-items-center rounded-tile bg-present-soft text-present">
        <ClockIcon className="size-5" />
      </span>
      <div className="flex flex-col gap-1">
        <p className="text-sm font-semibold text-ink-soft">Attendance Today</p>
        <p className="text-base font-bold text-ink">
          {record ? `Checked in at ${formatTime(record.check_in_at)}` : "Not checked in yet"}
        </p>
        {record?.check_out_at ? (
          <p className="text-xs text-muted">{`Checked out at ${formatTime(record.check_out_at)}`}</p>
        ) : null}
      </div>
    </Card>
  );
}

function LeaveBalanceCard({ balances }: { balances: LeaveBalance[] }) {
  const primary = balances[0] ?? null;
  if (!primary) {
    return (
      <Card className="flex h-full flex-col p-4 sm:p-6">
        <EmptyState
          title="No leave types defined yet"
          description="Ask an HR administrator to set up leave types before applying."
        />
      </Card>
    );
  }
  const days = primary.balance_half_days / 2;
  return (
    <Card className="flex h-full flex-col gap-3 p-4 sm:p-6">
      <span aria-hidden className="grid size-10 place-items-center rounded-tile bg-info-soft text-info">
        <CalendarCheckIcon className="size-5" />
      </span>
      <div className="flex flex-col gap-1">
        <p className="text-sm font-semibold text-ink-soft">Leave Balance</p>
        <p className="text-2xl font-bold text-ink tnum">
          {days.toLocaleString("en-IN", { maximumFractionDigits: 1 })}{" "}
          <span className="text-sm font-normal text-muted">days</span>
        </p>
        <p className="text-xs text-muted">{primary.leave_type_code}</p>
      </div>
    </Card>
  );
}
