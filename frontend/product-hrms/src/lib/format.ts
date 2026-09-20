import type { AttendanceStatus, EmployeeStatus } from "@/api/types";

/**
 * Zone-free label lookups — copy for enums, not time.
 *
 * Every timestamp in this app renders through `@opsgrids/ui`'s zone-aware helpers
 * (`formatInstant`, `formatInZone`, `formatRelative`, `resolveZone`) and `useZoneLookup`
 * (`@/lib/zone`), never through a formatter that hard-codes a zone. This file used to hard-code
 * `timeZone: "UTC"` for every date and time in the app — ADR-0000 requires the *relevant
 * location's* zone, and UTC is a location no record has (screens spec §2.1, gap G9).
 */

/** 512 -> "8h 32m". */
export function formatMinutes(minutes: number | null): string {
  if (minutes === null) return "—";
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return `${h}h ${m.toString().padStart(2, "0")}m`;
}

const STATUS_LABELS: Record<EmployeeStatus, string> = {
  probation: "Probation",
  active: "Active",
  on_notice: "On Notice",
  exited: "Exited",
};

export function employeeStatusLabel(status: EmployeeStatus): string {
  return STATUS_LABELS[status];
}

const EMPLOYMENT_LABELS: Record<string, string> = {
  full_time: "Full-time",
  part_time: "Part-time",
  contract: "Contract",
  intern: "Intern",
};

export function employmentLabel(type: string): string {
  return EMPLOYMENT_LABELS[type] ?? type;
}

/** The service's four statuses (screens spec gap **G13**): `present | half_day | absent |
 *  regularized`, not the SPA's earlier, wrong `present | late | absent`. */
const ATTENDANCE_STATUS_LABELS: Record<AttendanceStatus, string> = {
  present: "Present",
  half_day: "Half day",
  absent: "Absent",
  regularized: "Regularised",
};

export function attendanceStatusLabel(status: AttendanceStatus): string {
  return ATTENDANCE_STATUS_LABELS[status];
}
