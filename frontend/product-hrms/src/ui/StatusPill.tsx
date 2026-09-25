import type { AttendanceStatus, EmployeeStatus } from "@/api/types";
import { StatusBadge } from "@opsgrids/ui";

/**
 * Attendance status (screens spec gap **G13**): a thin wrapper over the shared `StatusBadge`, so
 * the word looks the same in every app that shows it. `half_day` and `regularized` aren't in the
 * shared registry (`@opsgrids/ui` lib/status.ts) — they're HRMS-specific words — so they take an
 * explicit tone here rather than a sixth colour.
 */
export function AttendancePill({ status }: { status: AttendanceStatus }) {
  if (status === "half_day") return <StatusBadge tone="warning" label="Half day" />;
  if (status === "regularized") return <StatusBadge tone="info" label="Regularized" />;
  return <StatusBadge status={status} />;
}

/**
 * Employee lifecycle status. `probation` isn't a registered word, so it takes an explicit tone;
 * `active`, `on_notice` and `exited` are, and get the registry's tone for free.
 */
export function EmployeeStatusPill({ status }: { status: EmployeeStatus }) {
  if (status === "probation") return <StatusBadge tone="info" label="Probation" />;
  return <StatusBadge status={status} label={status === "on_notice" ? "On Notice" : undefined} />;
}
