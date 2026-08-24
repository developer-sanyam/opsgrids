/**
 * Department-name resolution for an employee.
 *
 * The employee service stores `department_id` (an account-service org-unit UUID) and never a
 * name — ADR-0000 forbids the cross-schema join. Names come from the account service's
 * `GET /api/v1/org-units` tree, flattened to an `id -> name` map (see `@/api/orgUnits`). That tree
 * is permission-gated and often empty (no units seeded), so every employee also carries a human
 * `job_title` that names their functional group, which we fall back to. One resolver backs both
 * the dashboard's headcount rollup and the employee/attendance tables so their labels never
 * diverge.
 */

/** The fields any labelled record must expose — an employee row, or an attendance punch. */
export interface HasDepartment {
  department_id: string | null;
  job_title: string | null;
}

/**
 * Resolve one record's department label: prefer the live org-unit name (when `orgUnitNameById`
 * holds its `department_id`), fall back to the trimmed `job_title` (where the demo data's
 * department name actually lives), then to `fallback`. Never blocks on resolving the id.
 */
export function resolveDepartment(
  record: HasDepartment,
  orgUnitNameById?: ReadonlyMap<string, string>,
  fallback = "Unassigned",
): string {
  if (record.department_id !== null) {
    const live = orgUnitNameById?.get(record.department_id);
    if (live) return live;
  }
  return record.job_title?.trim() || fallback;
}

/**
 * A display label for a record's team in a table cell — the resolved department, or an em dash
 * when nothing resolves. The tables don't fetch the org-unit tree, so they resolve through the
 * `job_title` fallback; the dashboard passes a live map into `resolveDepartment` directly.
 */
export function departmentLabel(record: HasDepartment): string {
  return resolveDepartment(record, undefined, "—");
}
