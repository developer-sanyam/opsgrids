/**
 * Pure rollups over the live employee list. No I/O, no React — just grouping and percentages,
 * so the dashboard's live widgets (KPIs, headcount-by-department, workforce-mix) all derive from
 * one `GET /api/v1/employees` payload. Kept pure so it is trivially testable.
 */
import type {
  DeptHeadcount,
  EmployeeOut,
  EmploymentType,
  WorkforceSlice,
} from "@/api/types";
import { departmentName } from "./departments";
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
 * Headcount grouped by department, largest first. `department_id` resolves through
 * `departments.ts`; anything unresolved collapses into a single "—" bucket. Returns the top
 * `limit` departments (default 5).
 */
export function headcountByDepartment(
  employees: readonly EmployeeOut[],
  limit = 5,
): DeptHeadcount[] {
  const counts = new Map<string, number>();
  for (const emp of employees) {
    const known = emp.department_id ? departmentName(emp.department_id) : null;
    const name = known && known !== "Unassigned" ? known : "—";
    counts.set(name, (counts.get(name) ?? 0) + 1);
  }
  const total = employees.length;
  return [...counts.entries()]
    .map(([name, count]) => ({ key: name, name, count, pct: pct(count, total) }))
    .sort((a, b) => b.count - a.count || a.name.localeCompare(b.name))
    .slice(0, limit);
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
