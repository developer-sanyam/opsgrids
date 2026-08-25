import { useCallback, useMemo, useState } from "react";
import type { CSSProperties } from "react";
import { Link, useNavigate } from "react-router-dom";

import { getAllEmployees, listAttendance } from "@/api/client";
import { getOrgUnitNameById } from "@/api/orgUnits";
import type { EmployeeOut } from "@/api/types";
import { useAuth } from "@atmora/client";
import {
  activeCount,
  distinctDepartmentCount,
  headcountByDepartment,
  workforceMix,
} from "@/data/derive";
import { RECENT_ACTIVITY, SAMPLE_ATTENDANCE, TASKS_TODAY } from "@/data/samples";
import { useAsync } from "@atmora/client";
import {
  Button,
  Card,
  EmployeesPanel,
  ErrorState,
  HeadcountByDepartment,
  HeadcountTrend,
  KpiCard,
  RecentActivity,
  SampleChip,
  SectionHeader,
  Skeleton,
  TasksToday,
  WorkforceMix,
} from "@/ui";
import type { KpiTone, MiniStat } from "@/ui";
import {
  BellIcon,
  CalendarCheckIcon,
  CalendarIcon,
  CheckSquareIcon,
  LeafIcon,
  PlusIcon,
  SearchIcon,
  UsersIcon,
} from "@atmora/ui";
import { SAMPLE_DATA } from "@/ui/sampleCopy";

const TODAY = "2026-08-24";

/** A stable empty reference so the derived-data memos don't churn while loading. */
const EMPTY_EMPLOYEES: EmployeeOut[] = [];

/** A stable empty org-unit map, used while the tree loads or when the org has none to read. */
const EMPTY_ORG_UNITS: ReadonlyMap<string, string> = new Map();

type Period = "weekly" | "monthly" | "quarterly";

const PERIOD_RANGE: Record<Period, string> = {
  weekly: "18 Aug – 24 Aug 2026",
  monthly: "01 Aug – 24 Aug 2026",
  quarterly: "Jul – Sep 2026",
};

/** The Dashboard's top bar: breadcrumb + title, then the search / date / period / add controls. */
function DashboardHeader({ orgName }: { orgName: string }) {
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [period, setPeriod] = useState<Period>("monthly");

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
        <form
          onSubmit={(e) => {
            e.preventDefault();
            const q = query.trim();
            navigate(q ? `/employees?q=${encodeURIComponent(q)}` : "/employees");
          }}
          className="hidden h-11 min-w-0 items-center gap-2 rounded-full bg-surface px-4 shadow-card focus-within:outline focus-within:outline-2 focus-within:outline-accent focus-within:outline-offset-2 md:flex md:w-60"
        >
          <SearchIcon className="size-[18px] shrink-0 text-faint" />
          <label htmlFor="dash-search" className="sr-only">
            Search employees
          </label>
          <input
            id="dash-search"
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search employees…"
            className="min-w-0 flex-1 bg-transparent text-sm text-ink placeholder:text-faint focus:outline-none"
          />
        </form>

        <Link
          to="/notifications"
          aria-label="Notifications"
          className="relative grid size-11 shrink-0 place-items-center rounded-full bg-surface text-ink-soft shadow-card hover:bg-ground transition-colors"
        >
          <BellIcon className="size-5" />
          <span className="absolute right-2.5 top-2.5 size-2 rounded-full bg-accent ring-2 ring-surface" aria-hidden />
        </Link>

        <span className="hidden h-11 items-center gap-2 rounded-full bg-surface px-4 text-sm font-semibold text-ink-soft shadow-card sm:inline-flex">
          <CalendarIcon className="size-4 text-muted" />
          <span className="tnum">{PERIOD_RANGE[period]}</span>
        </span>

        <label className="inline-flex h-11 items-center rounded-full bg-surface px-2 shadow-card">
          <span className="sr-only">Reporting period</span>
          <select
            value={period}
            onChange={(e) => setPeriod(e.target.value as Period)}
            className="h-full cursor-pointer rounded-full bg-transparent px-2 text-sm font-semibold text-ink focus:outline-none"
          >
            <option value="weekly">Weekly</option>
            <option value="monthly">Monthly</option>
            <option value="quarterly">Quarterly</option>
          </select>
        </label>

        <Button
          size="md"
          leadingIcon={<PlusIcon className="size-4" />}
          onClick={() => navigate("/employees")}
        >
          Add Employee
        </Button>
      </div>
    </header>
  );
}

interface KpiConfig {
  key: string;
  label: string;
  value: string;
  deltaPct: number;
  series: number[];
  icon: React.ReactNode;
  tone: KpiTone;
  sample: boolean;
}

function buildKpis(
  employees: EmployeeOut[],
  total: number,
  attendance: { rate: string; onLeave: number } | null,
): KpiConfig[] {
  const active = activeCount(employees);
  return [
    {
      key: "total",
      label: "Total Employees",
      value: String(total),
      deltaPct: 4.2,
      series: [18, 19, 20, 21, 21, 23, total],
      icon: <UsersIcon className="size-5" />,
      tone: "accent",
      sample: false,
    },
    {
      key: "active",
      label: "Active",
      value: String(active),
      deltaPct: 2.9,
      series: [15, 16, 17, 18, 18, 19, active],
      icon: <CheckSquareIcon className="size-5" />,
      tone: "positive",
      sample: false,
    },
    {
      key: "attendance",
      label: "Attendance Rate",
      value: attendance ? attendance.rate : `${SAMPLE_ATTENDANCE.ratePct}%`,
      deltaPct: SAMPLE_ATTENDANCE.rateDelta,
      series: [90, 92, 91, 93, 94, 93, 94],
      icon: <CalendarCheckIcon className="size-5" />,
      tone: "info",
      sample: true,
    },
    {
      key: "on_leave",
      label: "On Leave",
      value: attendance ? String(attendance.onLeave) : "—",
      deltaPct: SAMPLE_ATTENDANCE.onLeaveDelta,
      series: [4, 3, 5, 3, 4, 3, attendance?.onLeave ?? 3],
      icon: <LeafIcon className="size-5" />,
      tone: "cyan",
      sample: true,
    },
  ];
}

export function DashboardPage() {
  const { user } = useAuth();
  const employees = useAsync(getAllEmployees, []);
  const attendance = useAsync(useCallback(() => listAttendance(TODAY), []), []);
  // Department names come from the org-unit tree. It is permission-gated and often empty, and its
  // loader already degrades any failure to an empty map — so a 403 here is never a dashboard error,
  // just names that fall back to each employee's job_title.
  const orgUnits = useAsync(getOrgUnitNameById, []);
  const orgUnitMap =
    orgUnits.status === "success" ? orgUnits.data : EMPTY_ORG_UNITS;

  const orgName =
    user?.organizations.find((o) => o.organization_id === user.organization_id)?.name ??
    user?.organizations[0]?.name ??
    "Atmora";

  const attendanceSummary = useMemo(() => {
    if (attendance.status !== "success") return null;
    const records = attendance.data;
    const present = records.filter((r) => r.status !== "absent").length;
    const onLeave = records.filter((r) => r.status === "absent").length;
    const rate = records.length === 0 ? 0 : (present / records.length) * 100;
    return { rate: `${rate.toFixed(1)}%`, onLeave };
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
                <KpiCard
                  label={kpi.label}
                  value={kpi.value}
                  deltaPct={kpi.deltaPct}
                  series={kpi.series}
                  icon={kpi.icon}
                  tone={kpi.tone}
                  sample={kpi.sample ? SAMPLE_DATA : false}
                />
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
            <HeadcountTrend current={total} sample />
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

      {/* Row 3 — employees table · activity + tasks */}
      <section aria-label="Team detail" className="grid grid-cols-1 gap-5 xl:grid-cols-3 items-start">
        <Card className="flex flex-col gap-4 p-6 xl:col-span-2">
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
          {employees.status === "success" ? <EmployeesPanel employees={items} /> : null}
        </Card>

        <div className="flex flex-col gap-5">
          <Card className="flex flex-col gap-5 p-6">
            <SectionHeader
              title="Recent Activity"
              action={
                <div className="flex items-center gap-2">
                  <SampleChip {...SAMPLE_DATA} />
                  <Link
                    to="/reports"
                    className="text-sm font-semibold text-accent hover:text-accent-strong transition-colors"
                  >
                    View all
                  </Link>
                </div>
              }
            />
            <RecentActivity items={RECENT_ACTIVITY} />
          </Card>

          <Card className="flex flex-col gap-4 p-6">
            <SectionHeader title="Tasks Today" action={<SampleChip {...SAMPLE_DATA} />} />
            <TasksToday tasks={TASKS_TODAY} />
          </Card>
        </div>
      </section>
    </div>
  );
}
