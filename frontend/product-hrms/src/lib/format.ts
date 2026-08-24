import type { EmployeeStatus } from "@/api/types";

/**
 * "2026-08-24T09:24:00Z" -> "9:24 AM". Returns "—" for a missing punch.
 * Rendered in UTC so a stored wall-clock time shows as-is, independent of the viewer's zone.
 */
export function formatTime(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleTimeString("en-IN", {
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
    timeZone: "UTC",
  });
}

/** "2021-03-15" -> "15 Mar 2021". */
export function formatDate(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleDateString("en-IN", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  });
}

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

/** Initials for an avatar fallback: "Ananya Iyer" -> "AI". */
export function initials(name: string): string {
  const parts = name.trim().split(/\s+/);
  const first = parts[0]?.[0] ?? "";
  const last = parts.length > 1 ? (parts[parts.length - 1]?.[0] ?? "") : "";
  return (first + last).toUpperCase();
}

/** Format a delta like 4.2 -> "+4.2%" / -1.4 -> "−1.4%". */
export function formatDelta(pct: number): string {
  const sign = pct >= 0 ? "+" : "−";
  return `${sign}${Math.abs(pct).toFixed(1)}%`;
}
