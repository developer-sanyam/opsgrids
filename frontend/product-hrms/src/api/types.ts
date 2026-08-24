/**
 * Wire types for the HRMS product.
 *
 * `EmployeeOut` and its enums mirror `services/employee/app/schemas.py` field-for-field, so a
 * generated client can drop in later without the UI changing. Attendance has no service on this
 * branch yet, so `AttendanceRecord` / `DashboardStats` are modelled from the plan (check-in/out,
 * status present|late|absent, work_date) and shaped like the responses the service will return.
 */

export type EmploymentType = "full_time" | "part_time" | "contract" | "intern";
export type WorkMode = "office" | "field" | "remote" | "hybrid";
export type EmployeeStatus = "probation" | "active" | "on_notice" | "exited";

/** Masked PII — the default read shape. Never plaintext, never ciphertext. */
export interface EmployeePiiMasked {
  date_of_birth: string | null;
  personal_email: string | null;
  personal_phone: string | null;
  pan: string | null;
  aadhaar: string | null;
  uan: string | null;
  esi: string | null;
  bank_account: string | null;
  bank_ifsc: string | null;
}

/** Mirrors `EmployeeOut`. `department_id` is a UUID; names are resolved client-side. */
export interface EmployeeOut {
  id: string;
  membership_id: string | null;
  employee_no: string;
  full_name: string;
  designation: string | null;
  job_title: string | null;
  department_id: string | null;
  location_code: string | null;
  employment_type: EmploymentType;
  work_mode: WorkMode;
  date_of_joining: string | null;
  reporting_manager_id: string | null;
  status: EmployeeStatus;
  date_of_exit: string | null;
  created_at: string;
  pii: EmployeePiiMasked | null;
}

/** Small envelope every list endpoint returns. */
export interface Page<T> {
  items: T[];
  total: number;
}

export type AttendanceStatus = "present" | "late" | "absent";
export type PunchSource = "web" | "mobile" | "biometric" | "field";

/** A single day's attendance for one employee. Modelled from the plan. */
export interface AttendanceRecord {
  id: string;
  employee_id: string;
  employee_no: string;
  full_name: string;
  department_id: string | null;
  work_date: string;
  check_in: string | null;
  check_out: string | null;
  worked_minutes: number | null;
  source: PunchSource;
  status: AttendanceStatus;
}

/** A tiny series used to draw a stat card's inline chart. */
export interface StatSeriesPoint {
  label: string;
  value: number;
}

export interface StatCardData {
  key: "total_employees" | "present_today" | "on_leave" | "pending_approvals";
  label: string;
  value: number;
  /** Percentage change vs. the prior period; sign drives the arrow tone. */
  delta_pct: number;
  series: number[];
}

export interface WeekdayAttendance {
  label: string;
  present: number;
  /** The single highlighted bar (today). */
  highlight: boolean;
}

export interface DashboardStats {
  greeting_name: string;
  cards: StatCardData[];
  week: WeekdayAttendance[];
}
