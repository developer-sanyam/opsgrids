/**
 * Department reference data. The employee service stores `department_id` (a UUID) and no name,
 * so the product resolves names here. When an org-units endpoint exists this becomes a fetch.
 */
export interface Department {
  id: string;
  name: string;
}

export const DEPARTMENTS: Department[] = [
  { id: "d1a00000-0000-4000-8000-000000000001", name: "Engineering" },
  { id: "d1a00000-0000-4000-8000-000000000002", name: "Sales" },
  { id: "d1a00000-0000-4000-8000-000000000003", name: "HR" },
  { id: "d1a00000-0000-4000-8000-000000000004", name: "Finance" },
  { id: "d1a00000-0000-4000-8000-000000000005", name: "Operations" },
];

const BY_ID = new Map(DEPARTMENTS.map((d) => [d.id, d]));

export function departmentName(id: string | null): string {
  if (id === null) return "Unassigned";
  return BY_ID.get(id)?.name ?? "Unassigned";
}

/**
 * A display label for an employee's team. The live employee service stores `department_id` as an
 * account-service UUID we don't resolve here (ADR: no cross-schema joins); it also carries a
 * human `job_title` that names the functional group. Prefer a resolved department name, fall back
 * to the job title, then to an em dash — never block on resolving the id.
 */
export function departmentLabel(emp: {
  department_id: string | null;
  job_title: string | null;
}): string {
  if (emp.department_id !== null) {
    const known = BY_ID.get(emp.department_id);
    if (known) return known.name;
  }
  return emp.job_title?.trim() || "—";
}
