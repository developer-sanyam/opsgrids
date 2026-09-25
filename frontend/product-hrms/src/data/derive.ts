/**
 * Pure rollups over the live employee list. No I/O, no React — just grouping and percentages,
 * so the dashboard's live widgets (KPIs, headcount-by-department, workforce-mix) all derive from
 * one `GET /api/v1/employees` payload. Kept pure so it is trivially testable.
 */
import type {
  DeptHeadcount,
  EmployeeOut,
  EmploymentType,
  TrendPoint,
  TrendRange,
  WorkforceSlice,
} from "@/api/types";
import { resolveDepartment } from "./departments";
import { employmentLabel } from "@/lib/format";

/** How many of these employees are in the `active` lifecycle state. */
export function activeCount(employees: readonly EmployeeOut[]): number {
  return employees.filter((e) => e.status === "active").length;
}

/** How many carry a given lifecycle status. */
export function countByStatus(
  employees: readonly EmployeeOut[],
  status: EmployeeOut["status"],
): number {
  return employees.filter((e) => e.status === status).length;
}

function pct(part: number, whole: number): number {
  return whole === 0 ? 0 : Math.round((part / whole) * 100);
}

/**
 * Headcount grouped by department, largest first. Each employee resolves through
 * `resolveDepartment`: a live org-unit name (from `orgUnitNameById`) when its `department_id` is
 * populated, else the `job_title` that names the functional group, else "Unassigned" — so the
 * bars stay named even when the org has no org-units seeded and `department_id` is null. Returns
 * the top `limit` departments (default 5).
 */
export function headcountByDepartment(
  employees: readonly EmployeeOut[],
  orgUnitNameById: ReadonlyMap<string, string>,
  limit = 5,
): DeptHeadcount[] {
  const counts = new Map<string, number>();
  for (const emp of employees) {
    const name = resolveDepartment(emp, orgUnitNameById);
    counts.set(name, (counts.get(name) ?? 0) + 1);
  }
  const total = employees.length;
  return [...counts.entries()]
    .map(([name, count]) => ({ key: name, name, count, pct: pct(count, total) }))
    .sort((a, b) => b.count - a.count || a.name.localeCompare(b.name))
    .slice(0, limit);
}

/**
 * How many distinct departments these employees resolve to, using the same rules as the headcount
 * bars (live org-unit name, else `job_title`, else "Unassigned"). Powers the "Departments" stat.
 */
export function distinctDepartmentCount(
  employees: readonly EmployeeOut[],
  orgUnitNameById: ReadonlyMap<string, string>,
): number {
  const names = new Set<string>();
  for (const emp of employees) names.add(resolveDepartment(emp, orgUnitNameById));
  return names.size;
}

const EMPLOYMENT_ORDER: EmploymentType[] = [
  "full_time",
  "part_time",
  "contract",
  "intern",
];

/** The workforce mix grouped by employment type — the donut's slices, in a stable order. */
export function workforceMix(employees: readonly EmployeeOut[]): WorkforceSlice[] {
  const total = employees.length;
  return EMPLOYMENT_ORDER.map((type) => {
    const count = employees.filter((e) => e.employment_type === type).length;
    return {
      key: type,
      label: employmentLabel(type),
      count,
      pct: pct(count, total),
    };
  }).filter((slice) => slice.count > 0);
}

/** The last day of each of the `count` periods ending with `now`, most recent last. The most
 *  recent bucket ends at `now` itself so its count matches today's live headcount; every earlier
 *  bucket ends on the last calendar day of its period. */
function periodEnds(range: TrendRange, now: Date): { label: string; end: Date }[] {
  if (range === "monthly") {
    return Array.from({ length: 12 }, (_, i) => {
      const back = 11 - i;
      const isCurrent = back === 0;
      const end = isCurrent ? now : new Date(now.getFullYear(), now.getMonth() - back + 1, 0);
      return { label: end.toLocaleDateString("en-US", { month: "short" }), end };
    });
  }
  if (range === "quarterly") {
    const currentQuarter = Math.floor(now.getMonth() / 3);
    return Array.from({ length: 4 }, (_, i) => {
      const back = 3 - i;
      const q = currentQuarter - back;
      const year = now.getFullYear() + Math.floor(q / 4);
      const qIndex = ((q % 4) + 4) % 4;
      const isCurrent = back === 0;
      const end = isCurrent ? now : new Date(year, qIndex * 3 + 3, 0);
      return { label: `Q${qIndex + 1} ${year}`, end };
    });
  }
  return Array.from({ length: 5 }, (_, i) => {
    const back = 4 - i;
    const year = now.getFullYear() - back;
    const isCurrent = back === 0;
    const end = isCurrent ? now : new Date(year, 11, 31);
    return { label: String(year), end };
  });
}

/**
 * Headcount at the end of each period, counted from each employee's own `date_of_joining` and
 * `date_of_exit` — no fabricated series. An employee with no `date_of_joining` on record can't be
 * placed on the timeline and is excluded from every point (they still count in the live "Total
 * Employees" KPI, which reads status alone).
 */
export function headcountTrend(
  employees: readonly EmployeeOut[],
  range: TrendRange,
  now: Date = new Date(),
): TrendPoint[] {
  const periods = periodEnds(range, now);
  return periods.map(({ label, end }) => {
    const value = employees.filter((e) => {
      if (!e.date_of_joining) return false;
      if (new Date(e.date_of_joining) > end) return false;
      if (e.date_of_exit && new Date(e.date_of_exit) <= end) return false;
      return true;
    }).length;
    return { label, value };
  });
}

/** Percent change between the last two points of a real series, or `null` when there's nothing
 *  sound to compare (fewer than two points, or a zero base). A delta is never invented. */
export function trendDeltaPct(series: readonly TrendPoint[]): number | null {
  if (series.length < 2) return null;
  const prev = series[series.length - 2];
  const curr = series[series.length - 1];
  if (!prev || !curr || prev.value === 0) return null;
  return ((curr.value - prev.value) / prev.value) * 100;
}
