/**
 * Wire types for the HRMS product.
 *
 * `EmployeeOut` and its enums mirror `services/employee/app/schemas.py` field-for-field, so a
 * generated client can drop in later without the UI changing. The attendance and approval shapes
 * below mirror `services/attendance/app/schemas.py` and `services/approval/app/schemas.py`
 * field-for-field too — those two services are live as of this slice (ADR-0006 §11, the
 * approval-engine screens spec). Only the daily punch roster (`AttendanceRecord`, below) stays
 * representative: the punch-level detail (check-in/out, source, hours) that widget renders has no
 * live equivalent in `AttendanceOut` yet (see `api/client.ts`), so it is generated, deterministic
 * sample data, labelled as such wherever it renders.
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

/** Small envelope a few list endpoints return. */
export interface Page<T> {
  items: T[];
  total: number;
}

/** `platform_core.pagination.Page` — every approval and attendance list carries these. */
export interface Paged<T> {
  items: T[];
  total: number;
  page: number;
  size: number;
  pages: number;
}

/* ------------------------------------------------------------------ locations, organization */

/** Mirrors account-service `LocationOut` — the zone-lookup source (screens spec §2.1). */
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

/**
 * The service's four statuses (`services/attendance/app/schemas.py`
 * `AttendanceStatusName`) — fixed here rather than the SPA's earlier, wrong
 * `present | late | absent` vocabulary.
 */
export type AttendanceStatus = "present" | "half_day" | "absent" | "regularized";
export type RegularizationStatus = "pending" | "approved" | "rejected" | "failed";

/** Punch sources the sample punch roster renders. Not part of the live attendance contract. */
export type PunchSource = "web" | "mobile" | "biometric" | "field";

/** Mirrors `RegularizationRequestOut` exactly — the latest correction request on a record. */
export interface RegularizationRequestOut {
  id: string;
  attendance_id: string;
  employee_id: string;
  requested_status: AttendanceStatus;
  /** The requester's own words, stored on attendance's own row — the source, not a copy (§4.C,
   *  §9.1 C3). Rendered on the requester's own row only; on other rows it is a density decision,
   *  not an access one — the request detail is one activation away. */
  reason: string;
  status: RegularizationStatus;
  approval_request_id: string | null;
  /** Set once `core.approval.request.opened` has been applied to this row. Null while nothing is
   *  known yet beyond "pending" — the true "has routing happened at all" signal, independent of
   *  `approval_request_id`, which a `.decided` event can also set first on replay. */
  approval_opened_at: string | null;
  /** The routed approver's employee id from `.opened`, or null. */
  approver_employee_id: string | null;
  /** The routed approver's display name from `.opened`, stored verbatim. Null in every case
   *  `approver_employee_id` is null (routing not yet known, or the step opened unassigned) *and*
   *  whenever approval published the event without the key at all — the client never invents a
   *  name it wasn't given. */
  approver_name: string | null;
  decided_at: string | null;
  /** The decider's comment from `.decided`, verbatim, or null if none was given or no decision
   *  has landed yet. */
  decision_comment: string | null;
  /** The decider's display name from `.decided`, stored verbatim. Null when the decider is not in
   *  approval's hierarchy copy (legitimate for an org-level decider) or the key was absent from
   *  the payload — never a reason to hide the row either way. */
  decided_by_name: string | null;
  failure_reason: string | null;
  created_at: string;
  /** `null` until `.opened` has landed (nothing known about routing yet); `true`/`false` after,
   *  mirroring `approver_employee_id === null` at that point — a step can open with no approver
   *  at all. Computed by the route at read time, not a stored column. */
  approver_unassigned: boolean | null;
  /** The punch's own location, or `null` when the punch carried none (falls back to
   *  labelled organization time — the same rule approval's responses follow). */
  location_code: string | null;
}

export interface RegularizationRequestIn {
  requested_status: AttendanceStatus;
  reason: string;
}

/** A single day's attendance for one employee — the punch roster's row shape.
 *  Representative data (§ above); the `id` is not a real attendance record id. */
export interface AttendanceRecord {
  id: string;
  employee_id: string;
  employee_no: string;
  full_name: string;
  department_id: string | null;
  /** The employee's functional group; carries the department name where `department_id` is null. */
  job_title: string | null;
  work_date: string;
  check_in: string | null;
  check_out: string | null;
  worked_minutes: number | null;
  source: PunchSource;
  status: AttendanceStatus;
  /** The record's own punch location — governs how its instants render (ADR-0000). */
  location_code: string | null;
  /** The latest correction request against this record, or `null` if none has ever been raised. */
  regularization: RegularizationRequestOut | null;
  /** What the server's own state machine permits from this record's current status. */
  allowed_transitions: AttendanceStatus[];
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

/** Mirrors `InboxItemOut` — one `GET /approvals/inbox` row. */
export interface InboxItemOut {
  id: string;
  request_type: string;
  source_ref: string;
  summary: RegularizationSummary;
  request_note: string | null;
  opened_at: string;
  requester_employee_id: string;
  requester_name: string | null;
  requester_employee_no: string | null;
  requester_location_code: string | null;
  inbox_reason: InboxReason;
  from_name: string | null;
  /**
   * The deadline of the step this row matched through — the server's own value, never recomputed
   * or defaulted here (§2.11: "the client never computes a due time"). `null` is a modelled state,
   * not missing data: an unassigned step has no approver to hold a deadline
   * (`ck_approval_steps_unassigned_has_no_due`), a request type with escalation switched off never
   * gets one, and a step opened before the SLA clock was armed keeps `null` forever. On an
   * `escalated_to_me` / `escalated_away` row this is the *successor* step's deadline — the one in
   * front of the reader now — never the closed predecessor's.
   */
  due_at: string | null;
}

/** Mirrors `RequestListItemOut` — one `GET /approvals/requests` row. */
export interface RequestListItemOut {
  id: string;
  request_type: string;
  source_ref: string;
  summary: RegularizationSummary;
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
  /** This step's own deadline, or `null` where there is none (§2.11). Never computed client-side. */
  due_at: string | null;
}

/** Mirrors `RequestDetailOut` — `GET /approvals/requests/{id}` and the decision response. */
export interface RequestDetailOut {
  id: string;
  request_type: string;
  source_ref: string;
  summary: RegularizationSummary;
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

/** A point on the headcount-trend chart (sample series). */
export interface TrendPoint {
  label: string;
  value: number;
}

export type TrendRange = "monthly" | "quarterly" | "yearly";

/** One entry in the Recent Activity timeline (sample). */
export interface ActivityItem {
  id: string;
  actor: string;
  action: string;
  target: string;
  when: string;
  tone: "accent" | "positive" | "info" | "late";
}

/** One row in Tasks Today (sample). */
export interface TaskItem {
  id: string;
  title: string;
  due: string;
  done: boolean;
}
