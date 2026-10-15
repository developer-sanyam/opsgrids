/**
 * Wire types for the HRMS product.
 *
 * `EmployeeOut` and its enums mirror `services/employee/app/schemas.py` field-for-field.
 * `AttendanceOut`, `ShiftOut`, `GeofenceOut` and the approval shapes mirror
 * `services/attendance/app/schemas.py` and `services/approval/app/schemas.py` field-for-field —
 * every one of these services is live. `AttendanceRecord` below is **not** a wire type: it is the
 * client-side join of one `AttendanceOut` row with the employee it belongs to, produced by
 * `listAttendanceRoster` in `api/client.ts` so the roster table has a name to render without every
 * row carrying its own employee fetch.
 */

export type EmploymentType = "full_time" | "part_time" | "contract" | "intern";
export type WorkMode = "office" | "field" | "remote" | "hybrid";
export type EmployeeStatus = "probation" | "active" | "on_notice" | "exited";
/** The non-terminal targets `POST /employees/{id}/transition` accepts — `probation` is an entry
 *  state nothing transitions *to*. */
export type EmployeeTransitionTarget = "active" | "on_notice" | "exited";

/** The sensitive fields the unmask endpoint may be asked to reveal. `bank_ifsc` is absent on
 *  purpose — it is not sensitive and is already returned whole on the masked view. */
export type SensitiveField =
  | "date_of_birth"
  | "personal_email"
  | "personal_phone"
  | "pan"
  | "aadhaar"
  | "uan"
  | "esi"
  | "bank_account";

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

/** Sensitive PII supplied as plaintext, sealed on write. Every field optional: on create it is
 *  what is known; on patch, present means "seal this new value", absent means "leave it alone". */
export interface EmployeePiiIn {
  date_of_birth?: string | null;
  personal_email?: string | null;
  personal_phone?: string | null;
  pan?: string | null;
  aadhaar?: string | null;
  uan?: string | null;
  esi?: string | null;
  bank_account?: string | null;
  bank_ifsc?: string | null;
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
  /** A second, informational reporting line — stored, never routed on. */
  dotted_line_manager_id: string | null;
  status: EmployeeStatus;
  date_of_exit: string | null;
  created_at: string;
  /** Bumped by the database whenever a hierarchy field changes. Read-only. */
  hierarchy_version: number;
  pii: EmployeePiiMasked | null;
}

/** `POST /employees` — `EmployeeIn`. `status` is never accepted: a new record always starts on
 *  probation. `membership_id` must never be sent — account owns that link (ADR-0008 §3). */
export interface EmployeeIn {
  /** Optional: omitted, the service generates the next free `EMP####`. */
  employee_no?: string;
  full_name: string;
  designation?: string | null;
  job_title?: string | null;
  department_id?: string | null;
  location_code?: string | null;
  employment_type?: EmploymentType;
  work_mode?: WorkMode;
  date_of_joining?: string | null;
  reporting_manager_id?: string | null;
  dotted_line_manager_id?: string | null;
  pii?: EmployeePiiIn;
}

/** `PATCH /employees/{id}` — `EmployeePatch`. Absent means "leave alone"; an explicit `null`
 *  clears the field. No `status` — the lifecycle moves only through `/transition`. */
export interface EmployeePatch {
  full_name?: string;
  designation?: string | null;
  job_title?: string | null;
  department_id?: string | null;
  location_code?: string | null;
  employment_type?: EmploymentType;
  work_mode?: WorkMode;
  date_of_joining?: string | null;
  /** Accepted only when the employee's current status is `exited`, and only on/after
   *  `date_of_joining` — otherwise a business-rule 422. */
  date_of_exit?: string | null;
  reporting_manager_id?: string | null;
  dotted_line_manager_id?: string | null;
  pii?: EmployeePiiIn;
}

export interface EmployeeTransitionIn {
  to: EmployeeTransitionTarget;
  reason?: string | null;
}

export interface PiiUnmaskIn {
  fields: SensitiveField[];
}

/** The only response that ever carries plaintext PII — never cached, rendered transiently. */
export interface EmployeePiiUnmasked {
  date_of_birth?: string | null;
  personal_email?: string | null;
  personal_phone?: string | null;
  pan?: string | null;
  aadhaar?: string | null;
  uan?: string | null;
  esi?: string | null;
  bank_account?: string | null;
}

/** `platform_core.pagination.Page` — every employee, attendance, shift, geofence and approval
 *  list carries these. */
export interface Paged<T> {
  items: T[];
  total: number;
  page: number;
  size: number;
  pages: number;
}

/* ------------------------------------------------------------------ locations, organization */

/** Mirrors account-service `LocationOut` — the zone-lookup source. */
export interface LocationOut {
  id: string;
  name: string;
  code: string;
  timezone: string;
  country: string | null;
  is_active: boolean;
}

/** The fields the zone fallback needs from account-service `OrganizationOut`. */
export interface OrganizationOut {
  id: string;
  name: string;
  timezone: string;
}

/* ------------------------------------------------------------------ attendance */

/** The service's four statuses (`AttendanceStatusName`). */
export type AttendanceStatus = "present" | "half_day" | "absent" | "regularized";
export type RegularizationStatus = "pending" | "approved" | "rejected" | "failed";

/** The wire punch source (`attendance.schemas.Source`). `"field"` is a `WorkMode`, never a
 *  source — filter field punches on `work_mode`, not on this. */
export type PunchSource = "web" | "mobile" | "biometric";

/** Mirrors `RegularizationRequestOut` exactly — the latest correction request on a record. */
export interface RegularizationRequestOut {
  id: string;
  attendance_id: string;
  employee_id: string;
  requested_status: AttendanceStatus;
  reason: string;
  status: RegularizationStatus;
  approval_request_id: string | null;
  approval_opened_at: string | null;
  approver_employee_id: string | null;
  approver_name: string | null;
  decided_at: string | null;
  decision_comment: string | null;
  decided_by_name: string | null;
  failure_reason: string | null;
  created_at: string;
  approver_unassigned: boolean | null;
  location_code: string | null;
}

export interface RegularizationRequestIn {
  requested_status: AttendanceStatus;
  reason: string;
}

/** Mirrors `AttendanceOut` field-for-field — one attendance record, exactly as the wire returns
 *  it. `check_in_at`/`check_out_at` are instants; nothing here is a display string yet. */
export interface AttendanceOut {
  id: string;
  employee_id: string;
  work_date: string;
  check_in_at: string;
  check_out_at: string | null;
  source: PunchSource;
  location_code: string | null;
  shift_id: string | null;
  work_mode: WorkMode;
  status: AttendanceStatus;
  check_in_lat: string | null;
  check_in_lng: string | null;
  check_out_lat: string | null;
  check_out_lng: string | null;
  notes: string | null;
  created_at: string;
  regularization: RegularizationRequestOut | null;
  allowed_transitions: AttendanceStatus[];
}

export interface CheckInIn {
  work_mode?: WorkMode;
  source?: PunchSource;
  location_code?: string | null;
  shift_id?: string | null;
  lat?: number | null;
  lng?: number | null;
  notes?: string | null;
}

export interface CheckOutIn {
  lat?: number | null;
  lng?: number | null;
}

/** `POST /attendance/{id}/regularize` — the HR direct correction. */
export interface RegularizeIn {
  to: AttendanceStatus;
  reason: string;
}

/** One day's attendance for one employee, joined client-side with the employee it belongs to —
 *  the punch roster's row shape. Produced by `listAttendanceRoster` (`api/client.ts`); never
 *  itself an endpoint response. `full_name` reads "Employee record unavailable" when the id in
 *  the attendance row doesn't resolve against the employee directory, rather than a blank. */
export interface AttendanceRecord {
  id: string;
  employee_id: string;
  employee_no: string;
  full_name: string;
  department_id: string | null;
  job_title: string | null;
  work_date: string;
  check_in: string | null;
  check_out: string | null;
  worked_minutes: number | null;
  source: PunchSource;
  work_mode: WorkMode;
  status: AttendanceStatus;
  location_code: string | null;
  regularization: RegularizationRequestOut | null;
  allowed_transitions: AttendanceStatus[];
}

/* ------------------------------------------------------------------ shifts & geofences */

export interface ShiftOut {
  id: string;
  code: string;
  name: string;
  start_time: string;
  end_time: string;
  grace_minutes: number;
  is_night: boolean;
  created_at: string;
}

export interface ShiftIn {
  code: string;
  name: string;
  start_time: string;
  end_time: string;
  grace_minutes?: number;
  is_night?: boolean;
}

/** `code` is immutable — "a rename is a new shift" (`services/attendance/app/schemas.py`). */
export interface ShiftPatch {
  name?: string;
  start_time?: string;
  end_time?: string;
  grace_minutes?: number;
  is_night?: boolean;
}

/** `center_lat`/`center_lng` travel as strings on the wire (`Decimal`) — sent and read as
 *  strings throughout the app so a coordinate never loses precision through a float round-trip. */
export interface GeofenceOut {
  id: string;
  location_code: string;
  name: string;
  center_lat: string;
  center_lng: string;
  radius_m: number;
  created_at: string;
}

export interface GeofenceIn {
  location_code: string;
  name: string;
  center_lat: string;
  center_lng: string;
  radius_m: number;
}

/** `location_code` is immutable — the punch match key. */
export interface GeofencePatch {
  name?: string;
  center_lat?: string;
  center_lng?: string;
  radius_m?: number;
}

/* ------------------------------------------------------------------ approval */

export type InboxReason =
  | "assigned"
  | "delegated_to_me"
  | "escalated_to_me"
  | "delegated_away"
  | "escalated_away"
  | "unassigned";

export type RequestStatus = "pending" | "approved" | "rejected";
export type ApplicationState = "applied" | "failed" | null;

/** `summary` for `request_type = "hrms.time.regularization"` — structured facts only (§2.9). */
export interface RegularizationSummary {
  date: string;
  from: AttendanceStatus;
  to: AttendanceStatus;
}

/** `summary` for `request_type = "hrms.time.leave"` (HRMS leave slice 1) — the same "structured
 *  facts only, no free text" rule ADR-0006 §3 states for regularisation's identical split;
 *  `leave.app.consumers.on_leave_requested` builds this exact shape. */
export interface LeaveApprovalSummary {
  leave_type_id: string;
  start_date: string;
  end_date: string;
  half_day_start: boolean;
  half_day_end: boolean;
  total_half_days: number;
}

/** The union every approval surface actually receives — discriminate on the sibling
 *  `request_type` field, never on shape alone (both objects are plain JSON with no `kind` tag). */
export type ApprovalSummary = RegularizationSummary | LeaveApprovalSummary;

/** Mirrors `InboxItemOut` — one `GET /approvals/inbox` row. */
export interface InboxItemOut {
  id: string;
  request_type: string;
  source_ref: string;
  summary: ApprovalSummary;
  request_note: string | null;
  opened_at: string;
  requester_employee_id: string;
  requester_name: string | null;
  requester_employee_no: string | null;
  requester_location_code: string | null;
  inbox_reason: InboxReason;
  from_name: string | null;
  due_at: string | null;
}

/** Mirrors `RequestListItemOut` — one `GET /approvals/requests` row. */
export interface RequestListItemOut {
  id: string;
  request_type: string;
  source_ref: string;
  summary: ApprovalSummary;
  status: RequestStatus;
  opened_at: string;
  requester_employee_id: string;
  requester_name: string | null;
  requester_employee_no: string | null;
  requester_location_code: string | null;
  approver_employee_id: string | null;
  approver_name: string | null;
  decided_by_employee_id: string | null;
  decided_by_name: string | null;
  decided_at: string | null;
  comment: string | null;
  application_state: ApplicationState;
  application_failure_reason: string | null;
}

/** Mirrors `StepOut` — one row of a request's append-only history. */
export interface StepOut {
  level: number;
  reason: string;
  outcome: string;
  approver_employee_id: string | null;
  approver_name: string | null;
  acting_for_employee_id: string | null;
  acting_for_name: string | null;
  opened_at: string;
  closed_at: string | null;
  due_at: string | null;
}

/** Mirrors `RequestDetailOut` — `GET /approvals/requests/{id}` and the decision response. */
export interface RequestDetailOut {
  id: string;
  request_type: string;
  source_ref: string;
  summary: ApprovalSummary;
  request_note: string | null;
  status: RequestStatus;
  opened_at: string;
  requester_employee_id: string;
  requester_name: string | null;
  requester_employee_no: string | null;
  requester_location_code: string | null;
  approver_employee_id: string | null;
  approver_name: string | null;
  acting_for_employee_id: string | null;
  acting_for_name: string | null;
  inbox_reason: InboxReason | null;
  from_name: string | null;
  decided_by_employee_id: string | null;
  decided_by_name: string | null;
  decided_at: string | null;
  comment: string | null;
  application_state: ApplicationState;
  application_failure_reason: string | null;
  applied_at: string | null;
  steps: StepOut[];
}

export interface DecisionIn {
  outcome: "approve" | "reject";
  comment?: string | null;
}

/** Mirrors `ChainRungOut`. */
export interface ChainRungOut {
  level: number;
  employee_id: string | null;
  display_name: string | null;
  employee_no: string | null;
  skipped: boolean;
  is_fallback: boolean;
}

/** Mirrors `DottedLineManagerOut`. */
export interface DottedLineManagerOut {
  employee_id: string;
  display_name: string | null;
  employee_no: string | null;
}

/** Mirrors `ApprovalChainOut`. */
export interface ApprovalChainOut {
  employee_id: string;
  location_code: string | null;
  chain: ChainRungOut[];
  dotted_line_manager: DottedLineManagerOut | null;
  unresolved: "cycle" | "depth_exceeded" | null;
}

/* ------------------------------------------------------------------ delegation (slice 3) */

/** Mirrors `DelegateCandidateOut` — one `GET /delegate-candidates` row. Three fields only: enough
 *  to tell two colleagues apart, never more (ADR-0006 §11). */
export interface DelegateCandidateOut {
  employee_id: string;
  display_name: string;
  employee_no: string;
}

export type DelegationStatus = "active" | "scheduled" | "ended" | "revoked";

/** Mirrors `DelegationOut` — one `GET /delegations` row, and what create/revoke both return.
 *  Neither party carries `employee_no` (C6): a delegation row identifies nobody the way a
 *  request's requester does. */
export interface DelegationOut {
  id: string;
  delegator_employee_id: string;
  delegator_name: string | null;
  /** Where the delegator's day runs — governs every instant on the row (§2.1). */
  delegator_location_code: string | null;
  delegate_employee_id: string;
  delegate_name: string | null;
  starts_at: string;
  ends_at: string;
  /** Computed server-side from the database's own clock — never from the caller's browser clock. */
  status: DelegationStatus;
  created_by_employee_id: string;
  revoked_at: string | null;
  revoked_by_employee_id: string | null;
}

/** Mirrors `DelegationCreateOut` — `DelegationOut` plus the one field only a create response
 *  carries: how many currently open steps re-pointed to the delegate in the same transaction. `0`
 *  is common and ordinary; the client omits the "N requests moved" line rather than rendering
 *  "0 requests moved" (C4). */
export interface DelegationCreateOut extends DelegationOut {
  repointed_count: number;
}

/** Mirrors `DelegationCreateIn` — `POST /delegations`. Omitting `delegator_employee_id` means
 *  "for myself"; supplying a different id needs `delegation.write:ORG` (§4.E's "for another
 *  employee" mode). */
export interface DelegationCreateIn {
  delegator_employee_id?: string;
  delegate_employee_id: string;
  starts_at: string;
  ends_at: string;
}

/* ------------------------------------------------------------------ policy administration (slice 4) */

/** Mirrors `PolicyOut` — one `GET /policies` row (`services/approval/app/schemas.py`). A request
 *  type with nothing configured still appears here, at the server's own defaults
 *  (`domain.DEFAULT_SLA_MINUTES` = 1440, `DEFAULT_POLICY_ENABLED` = true) — never a row the
 *  client fabricates itself. Readable by anyone who can read requests, at any scope: the one read
 *  endpoint whose scope is not consulted at all (§5 row 15). */
export interface PolicyOut {
  request_type: string;
  sla_minutes: number;
  enabled: boolean;
}

/** Mirrors `PolicyUpdateIn` — `PUT /policies/{request_type}`, floored at `policy.write:ORG`.
 *  Bounds mirror the database CHECK exactly (15 minutes to 365 days); a value outside them is a
 *  field-level 422 on `sla_minutes`, an unknown `request_type` a field-level 422 on that field. */
export interface PolicyUpdateIn {
  sla_minutes: number;
  enabled: boolean;
}

/* --------------------------- dashboard rollups --------------------------- */

/** One department's share of headcount — grouped live from employees. */
export interface DeptHeadcount {
  key: string;
  name: string;
  count: number;
  /** Share of the resolved total, 0–100. */
  pct: number;
}

/** One slice of the workforce-mix donut — grouped live by employment type. */
export interface WorkforceSlice {
  key: string;
  label: string;
  count: number;
  pct: number;
}

/** A point on the headcount-trend chart, computed from real employee join/exit dates. */
export interface TrendPoint {
  label: string;
  value: number;
}

export type TrendRange = "monthly" | "quarterly" | "yearly";

/* ------------------------------------------------------------------------------------- leave */

export type LeaveRequestStatus = "pending" | "approved" | "rejected" | "cancelled" | "failed";
export type LeaveLedgerEntryType = "grant" | "accrual" | "consumption" | "reversal" | "adjustment";
/** What `POST /leave/balances/grant` accepts — `consumption` and `reversal` are posted only by
 *  the service itself, from the approval-decision and cancellation paths (never a client). */
export type GrantableLeaveEntryType = "grant" | "accrual" | "adjustment";

/** Mirrors `LeaveTypeOut`. */
export interface LeaveTypeOut {
  id: string;
  code: string;
  name: string;
  is_paid: boolean;
  annual_quota_half_days: number | null;
  monthly_accrual_half_days: number | null;
  carry_forward_cap_half_days: number | null;
  active: boolean;
  created_at: string;
}

/** Mirrors `LeaveTypeIn` — `POST /leave/types`. */
export interface LeaveTypeIn {
  code: string;
  name: string;
  is_paid?: boolean;
  annual_quota_half_days?: number | null;
  monthly_accrual_half_days?: number | null;
  carry_forward_cap_half_days?: number | null;
}

/** Mirrors `LeaveTypePatch` — `PATCH /leave/types/{id}`. Absent means "leave alone". */
export interface LeaveTypePatch {
  name?: string;
  is_paid?: boolean;
  annual_quota_half_days?: number | null;
  monthly_accrual_half_days?: number | null;
  carry_forward_cap_half_days?: number | null;
  active?: boolean;
}

/** Mirrors `LeaveBalanceOut` — one `(employee, leave type)` row of `GET /leave/balances`. */
export interface LeaveBalanceOut {
  employee_id: string;
  leave_type_id: string;
  leave_type_code: string;
  balance_half_days: number;
}

/** Mirrors `LeaveBalanceGrantIn` — `POST /leave/balances/grant`. */
export interface LeaveBalanceGrantIn {
  employee_id: string;
  leave_type_id: string;
  entry_type?: GrantableLeaveEntryType;
  half_days: number;
  idempotency_key?: string | null;
  note?: string | null;
}

/** Mirrors `LeaveLedgerEntryOut` — one row of `GET /leave/balances/ledger`. */
export interface LeaveLedgerEntryOut {
  id: string;
  employee_id: string;
  leave_type_id: string;
  entry_type: LeaveLedgerEntryType;
  delta_half_days: number;
  leave_request_id: string | null;
  note: string | null;
  created_at: string;
}

/** Mirrors `LeaveRequestIn` — `POST /leave/requests`. No `employee_id`: a person applies for
 *  their own leave, the same shape `CheckInIn` uses for a self-service write. */
export interface LeaveRequestIn {
  leave_type_id: string;
  start_date: string;
  end_date: string;
  half_day_start?: boolean;
  half_day_end?: boolean;
  reason: string;
}

/** Mirrors `LeaveCancelIn` — the body of both `.../cancel` and `.../force-cancel`. */
export interface LeaveCancelIn {
  reason?: string | null;
}

/** Mirrors `LeaveRequestOut`. */
export interface LeaveRequestOut {
  id: string;
  employee_id: string;
  leave_type_id: string;
  start_date: string;
  end_date: string;
  half_day_start: boolean;
  half_day_end: boolean;
  total_half_days: number;
  reason: string;
  status: LeaveRequestStatus;
  approval_request_id: string | null;
  approval_opened_at: string | null;
  approver_employee_id: string | null;
  approver_name: string | null;
  approver_unassigned: boolean | null;
  decided_at: string | null;
  decision_comment: string | null;
  decided_by_name: string | null;
  failure_reason: string | null;
  cancelled_at: string | null;
  cancellation_reason: string | null;
  created_at: string;
}

/** Mirrors `LeaveSettingsOut` — `GET /leave/settings`. */
export interface LeaveSettingsOut {
  organization_id: string;
  timezone: string;
  updated_at: string;
}

/** Mirrors `LeaveSettingsIn` — `PUT /leave/settings`. */
export interface LeaveSettingsIn {
  timezone: string;
}
