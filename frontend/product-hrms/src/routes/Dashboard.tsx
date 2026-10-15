import { useCallback, useMemo } from "react";
import type { CSSProperties } from "react";
import { Link, useNavigate } from "react-router-dom";

import { getAllEmployees, getAttendanceDaySummary } from "@/api/client";
import { getOrgUnitNameById } from "@/api/orgUnits";
import type { EmployeeOut, TrendPoint, TrendRange } from "@/api/types";
import { Can, orgToday, useAsync, useAuth } from "@opsgrids/client";
import {
  activeCount,
  distinctDepartmentCount,
  headcountByDepartment,
  headcountTrend,
  workforceMix,
} from "@/data/derive";
import {
  Button,
  Card,
  EmployeesPanel,
  EmptyState,
  ErrorState,
  HeadcountByDepartment,
  HeadcountTrend,
  KpiCard,
  SectionHeader,
  Skeleton,
  WorkforceMix,
} from "@/ui";
import type { KpiTone, MiniStat } from "@/ui";
import { ArrowRightIcon, CalendarCheckIcon, CalendarIcon, CheckSquareIcon, PlusIcon, UsersIcon } from "@opsgrids/ui";

/** The real clock — never a frozen fixture date. */
function todayISO(): string {
  return orgToday();
}

/** A stable empty reference so the derived-data memos don't churn while loading. */
const EMPTY_EMPLOYEES: EmployeeOut[] = [];

/** A stable empty org-unit map, used while the tree loads or when the org has none to read. */
const EMPTY_ORG_UNITS: ReadonlyMap<string, string> = new Map();

/** The Dashboard's top bar: breadcrumb + title, then the date / add controls. Every control here
 *  does real work — the old period select changed nothing and is gone, along with the
 *  notification bell's permanently-on unread dot (no notifications backend exists). The shell's
 *  own header already has a search that reaches Employees (`AppShell`'s `search.fallback`), so
 *  this doesn't repeat it. */
function DashboardHeader({ orgName }: { orgName: string }) {
  const navigate = useNavigate();
  const today = useMemo(() => new Date(), []);

  return (
    <header className="flex flex-col gap-4 xl:flex-row xl:items-end xl:justify-between">
      <div className="flex flex-col gap-1">
        <nav aria-label="Breadcrumb" className="text-sm font-medium text-muted">
          Workspace <span className="text-faint">/</span>{" "}
          <span className="text-ink-soft">{orgName}</span>
        </nav>
        <h1 className="display text-3xl font-extrabold leading-tight sm:text-[2.1rem]">
          Dashboard
        </h1>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <span className="hidden h-11 items-center gap-2 rounded-full bg-surface px-4 text-sm font-semibold text-ink-soft shadow-card sm:inline-flex">
          <CalendarIcon className="size-4 text-muted" />
          <span className="tnum">
            {today.toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" })}
          </span>
        </span>

        <Can permission="hrms.people.employee.write" scope="ORG">
          <Button
            size="md"
            leadingIcon={<PlusIcon className="size-4" />}
            onClick={() => navigate("/employees")}
          >
            Add Employee
          </Button>
        </Can>
      </div>
    </header>
  );
}

interface KpiConfig {
  key: string;
  label: string;
  value: string;
  icon: React.ReactNode;
  tone: KpiTone;
}

function buildKpis(
  employees: EmployeeOut[],
  total: number,
  attendance: { rate: string; absent: number } | null,
): KpiConfig[] {
  const active = activeCount(employees);
  return [
    {
      key: "total",
      label: "Total Employees",
      value: String(total),
      icon: <UsersIcon className="size-5" />,
      tone: "accent",
    },
    {
      key: "active",
      label: "Active",
      value: String(active),
      icon: <CheckSquareIcon className="size-5" />,
      tone: "positive",
    },
    {
      key: "attendance",
      label: "Attendance Rate Today",
      value: attendance ? attendance.rate : "—",
      icon: <CalendarCheckIcon className="size-5" />,
      tone: "info",
    },
    {
      key: "absent",
      label: "Absent Today",
      value: attendance ? String(attendance.absent) : "—",
      icon: <UsersIcon className="size-5" />,
      tone: "cyan",
    },
  ];
}

export function DashboardPage() {
  const { user } = useAuth();
  const employees = useAsync(getAllEmployees, []);
  const today = useMemo(() => todayISO(), []);
  const attendance = useAsync(useCallback(() => getAttendanceDaySummary(today), [today]), [today]);
  // Department names come from the org-unit tree. It is permission-gated and often empty, and its
  // loader already degrades any failure to an empty map — so a 403 here is never a dashboard error,
  // just names that fall back to each employee's job_title.
  const orgUnits = useAsync(getOrgUnitNameById, []);
  const orgUnitMap =
    orgUnits.status === "success" ? orgUnits.data : EMPTY_ORG_UNITS;

  const orgName =
    user?.organizations.find((o) => o.organization_id === user.organization_id)?.name ??
    user?.organizations[0]?.name ??
    "OpsGrids";

  const attendanceSummary = useMemo(() => {
    if (attendance.status !== "success") return null;
    const { total, presentOrHalfDay, absent } = attendance.data;
    const rate = total === 0 ? 0 : (presentOrHalfDay / total) * 100;
    return { rate: `${rate.toFixed(1)}%`, absent };
  }, [attendance]);

  const items = useMemo(
    () => (employees.status === "success" ? employees.data.items : EMPTY_EMPLOYEES),
    [employees],
  );
  const total = employees.status === "success" ? employees.data.total : 0;

  const kpis = useMemo(
    () => buildKpis(items, total, attendanceSummary),
    [items, total, attendanceSummary],
  );

  const departments = useMemo(
    () => headcountByDepartment(items, orgUnitMap),
    [items, orgUnitMap],
  );
  const mix = useMemo(() => workforceMix(items), [items]);
  const headcountSeries = useMemo<Record<TrendRange, TrendPoint[]>>(
    () => ({
      monthly: headcountTrend(items, "monthly"),
      quarterly: headcountTrend(items, "quarterly"),
      yearly: headcountTrend(items, "yearly"),
    }),
    [items],
  );

  const miniStats: MiniStat[] = useMemo(() => {
    const locations = new Set(items.map((e) => e.location_code).filter(Boolean)).size;
    const active = activeCount(items);
    const activeRate = total === 0 ? 0 : Math.round((active / total) * 100);
    const deptCount = distinctDepartmentCount(items, orgUnitMap);
    return [
      { label: "Departments", metric: "teams", total: String(deptCount) },
      { label: "Locations", metric: "sites", total: String(locations) },
      { label: "Active rate", metric: "of staff", total: `${activeRate}%` },
    ];
  }, [items, total, orgUnitMap]);

  return (
    <div className="rise flex flex-col gap-5">
      <DashboardHeader orgName={orgName} />

      {/* Row 1 — KPI cards */}
      <section aria-label="Key metrics" className="cascade grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {employees.status === "loading"
          ? Array.from({ length: 4 }).map((_, i) => (
              <Card key={i} className="flex flex-col gap-4 p-5" style={{ "--i": i } as CSSProperties}>
                <div className="flex justify-between">
                  <Skeleton className="size-10 rounded-tile" />
                  <Skeleton className="h-8 w-20" />
                </div>
                <Skeleton className="h-4 w-24" />
                <Skeleton className="h-9 w-20 rounded-tile" />
              </Card>
            ))
          : null}
        {employees.status === "error" ? (
          <Card className="p-5 sm:col-span-2 xl:col-span-4">
            <ErrorState message={employees.error.message} />
          </Card>
        ) : null}
        {employees.status === "success"
          ? kpis.map((kpi, i) => (
              <div key={kpi.key} style={{ "--i": i } as CSSProperties}>
                <KpiCard label={kpi.label} value={kpi.value} icon={kpi.icon} tone={kpi.tone} />
              </div>
            ))
          : null}
      </section>

      {/* Row 2 — department bars · trend · mix */}
      <section aria-label="Workforce overview" className="grid grid-cols-1 gap-5 xl:grid-cols-4">
        <Card className="flex flex-col gap-5 p-6 xl:col-span-1">
          <SectionHeader title="Headcount by Department" subtitle="Where people sit" />
          {employees.status === "success" && departments.length > 0 ? (
            <HeadcountByDepartment data={departments} total={total} />
          ) : employees.status === "error" ? (
            <ErrorState message={employees.error.message} />
          ) : (
            <Skeleton className="h-56 w-full rounded-tile" />
          )}
        </Card>

        <Card className="flex flex-col gap-4 p-6 xl:col-span-2">
          <SectionHeader title="Headcount Trend" subtitle="Team size over time" />
          {employees.status === "success" ? (
            <HeadcountTrend current={total} series={headcountSeries} />
          ) : (
            <Skeleton className="h-56 w-full rounded-tile" />
          )}
        </Card>

        <Card className="flex flex-col gap-5 p-6 xl:col-span-1">
          <SectionHeader title="Workforce Mix" subtitle="By employment type" />
          {employees.status === "success" && mix.length > 0 ? (
            <WorkforceMix slices={mix} total={total} stats={miniStats} />
          ) : employees.status === "error" ? (
            <ErrorState message={employees.error.message} />
          ) : (
            <Skeleton className="h-56 w-full rounded-tile" />
          )}
        </Card>
      </section>

      {/* Row 3 — employees table */}
      <section aria-label="Team detail" className="grid grid-cols-1 gap-5">
        <Card className="flex flex-col gap-4 p-6">
          <SectionHeader
            title="Employees"
            subtitle={
              employees.status === "success" ? `${total} people in the directory` : "Loading…"
            }
          />
          {employees.status === "loading" ? (
            <div className="flex flex-col gap-3 py-2">
              {Array.from({ length: 6 }).map((_, i) => (
                <Skeleton key={i} className="h-12 w-full rounded-tile" />
              ))}
            </div>
          ) : null}
          {employees.status === "error" ? (
            <ErrorState message={employees.error.message} />
          ) : null}
          {employees.status === "success" && items.length === 0 ? (
            <EmptyState
              icon={<UsersIcon className="size-6" />}
              title="No employees yet"
              description="Add your first employee to start the directory."
              action={
                <Can permission="hrms.people.employee.write" scope="ORG">
                  <Link
                    to="/employees"
                    className="inline-flex h-11 items-center rounded-full bg-accent px-6 font-semibold text-on-accent transition-colors hover:bg-accent-strong"
                  >
                    Add your first employee
                  </Link>
                </Can>
              }
            />
          ) : null}
          {employees.status === "success" && items.length > 0 ? (
            <>
              {/* The directory has its own page, built for a phone; a second, cramped copy of it
               *  on the dashboard is what this link replaces below 768px. */}
              <Link
                to="/employees"
                className="relative hit-44 inline-flex w-fit items-center gap-2 text-sm font-semibold text-accent hover:text-accent-strong md:hidden"
              >
                View all employees
                <ArrowRightIcon className="size-4" />
              </Link>
              <div className="hidden md:block">
                <EmployeesPanel employees={items} orgUnitMap={orgUnitMap} />
              </div>
            </>
          ) : null}
        </Card>
      </section>
    </div>
  );
}
