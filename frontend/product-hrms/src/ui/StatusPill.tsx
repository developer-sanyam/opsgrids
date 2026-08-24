import type { AttendanceStatus, EmployeeStatus } from "@/api/types";
import { cn } from "@/lib/cn";

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
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full pl-2 pr-2.5 py-1 text-xs font-semibold",
        ATTENDANCE_TONE[status],
      )}
    >
      <span className="size-1.5 rounded-full bg-current" aria-hidden />
      {ATTENDANCE_LABEL[status]}
    </span>
  );
}

const EMPLOYEE_TONE: Record<EmployeeStatus, string> = {
  active: "bg-present-soft text-present",
  probation: "bg-late-soft text-late",
  on_notice: "bg-danger-soft text-danger",
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
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full pl-2 pr-2.5 py-1 text-xs font-semibold",
        EMPLOYEE_TONE[status],
      )}
    >
      <span className="size-1.5 rounded-full bg-current" aria-hidden />
      {EMPLOYEE_LABEL[status]}
    </span>
  );
}
