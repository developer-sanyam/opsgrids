import type { AttendanceStatus, EmployeeStatus } from "@/api/types";
import { cn } from "@/lib/cn";

const PILL_BASE =
  "inline-flex items-center gap-1.5 rounded-full pl-2 pr-2.5 py-1 text-xs font-semibold";

const ATTENDANCE_TONE: Record<AttendanceStatus, string> = {
  present: "bg-present-soft text-present",
  late: "bg-late-soft text-late",
  absent: "bg-danger-soft text-danger",
};

const ATTENDANCE_LABEL: Record<AttendanceStatus, string> = {
  present: "Present",
  late: "Late",
  absent: "Absent",
};

/** Attendance status. A coloured dot backs up the colour so it never carries meaning alone. */
export function AttendancePill({ status }: { status: AttendanceStatus }) {
  return (
    <span className={cn(PILL_BASE, ATTENDANCE_TONE[status])}>
      <span className="size-1.5 rounded-full bg-current" aria-hidden />
      {ATTENDANCE_LABEL[status]}
    </span>
  );
}

/**
 * Employee lifecycle status, mapped to the semantic pill families:
 * Active = green, Probation = blue, On Notice = amber, Exited = grey.
 */
const EMPLOYEE_TONE: Record<EmployeeStatus, string> = {
  active: "bg-present-soft text-present",
  probation: "bg-info-soft text-info",
  on_notice: "bg-late-soft text-late",
  exited: "bg-ground text-muted",
};

const EMPLOYEE_LABEL: Record<EmployeeStatus, string> = {
  active: "Active",
  probation: "Probation",
  on_notice: "On Notice",
  exited: "Exited",
};

export function EmployeeStatusPill({ status }: { status: EmployeeStatus }) {
  return (
    <span className={cn(PILL_BASE, EMPLOYEE_TONE[status])}>
      <span className="size-1.5 rounded-full bg-current" aria-hidden />
      {EMPLOYEE_LABEL[status]}
    </span>
  );
}
