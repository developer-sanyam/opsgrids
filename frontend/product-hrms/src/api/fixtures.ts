/**
 * Realistic fixture data, shaped exactly like the real API responses so the whole UI renders
 * without a live login. Swapped out by the `USE_FIXTURES` flag in `client.ts`.
 */

import { ApiError } from "@opsgrids/client";
import type { ProblemJson } from "@opsgrids/client";

import type {
  ApprovalChainOut,
  AttendanceRecord,
  AttendanceStatus,
  DelegateCandidateOut,
  DelegationOut,
  EmployeeIn,
  EmployeeOut,
  EmployeePatch,
  EmployeePiiUnmasked,
  EmployeeTransitionIn,
  EmploymentType,
  GeofenceIn,
  GeofenceOut,
  GeofencePatch,
  InboxItemOut,
  LocationOut,
  OrganizationOut,
  PiiUnmaskIn,
  PolicyOut,
  RequestDetailOut,
  RequestListItemOut,
  ShiftIn,
  ShiftOut,
  ShiftPatch,
  StepOut,
  WorkMode,
  PunchSource,
} from "./types";

// This org, like the live demo org, has no org-units seeded: every employee's `department_id` is
// null and the department name lives in `job_title` (see `dept` below). Names resolve through the
// `job_title` fallback in `resolveDepartment`, not through the org-unit tree.
const ENGINEERING = "Engineering";
const SALES = "Sales";
const HR = "HR";
const FINANCE = "Finance";
const OPERATIONS = "Operations";

interface Seed {
  no: string;
  name: string;
  designation: string;
  /** The department name — stored in `job_title`, since `department_id` is unseeded (null). */
  dept: string;
  location: string;
  employment: EmploymentType;
  mode: WorkMode;
  joined: string;
  status: EmployeeOut["status"];
}

// 24 people. Varied, real-sounding names across five departments and four Indian metros.
const SEEDS: Seed[] = [
  { no: "ATM-0101", name: "Ananya Iyer", designation: "Staff Engineer", dept: ENGINEERING, location: "Bengaluru", employment: "full_time", mode: "hybrid", joined: "2021-03-15", status: "active" },
  { no: "ATM-0102", name: "Rohan Mehta", designation: "Senior Frontend Engineer", dept: ENGINEERING, location: "Pune", employment: "full_time", mode: "remote", joined: "2022-07-04", status: "active" },
  { no: "ATM-0103", name: "Priya Nair", designation: "Engineering Manager", dept: ENGINEERING, location: "Bengaluru", employment: "full_time", mode: "office", joined: "2020-01-20", status: "active" },
  { no: "ATM-0104", name: "Daniel Okafor", designation: "Backend Engineer", dept: ENGINEERING, location: "Bengaluru", employment: "full_time", mode: "hybrid", joined: "2023-09-11", status: "active" },
  { no: "ATM-0105", name: "Meera Krishnan", designation: "QA Engineer", dept: ENGINEERING, location: "Chennai", employment: "contract", mode: "remote", joined: "2024-02-19", status: "probation" },
  { no: "ATM-0106", name: "Arjun Reddy", designation: "DevOps Engineer", dept: ENGINEERING, location: "Hyderabad", employment: "full_time", mode: "hybrid", joined: "2022-11-28", status: "active" },
  { no: "ATM-0201", name: "Sofia Marchetti", designation: "Regional Sales Head", dept: SALES, location: "Mumbai", employment: "full_time", mode: "office", joined: "2019-06-10", status: "active" },
  { no: "ATM-0202", name: "Vikram Singh", designation: "Account Executive", dept: SALES, location: "Delhi", employment: "full_time", mode: "field", joined: "2023-04-02", status: "active" },
  { no: "ATM-0203", name: "Grace Adeyemi", designation: "Sales Development Rep", dept: SALES, location: "Mumbai", employment: "full_time", mode: "field", joined: "2024-05-13", status: "probation" },
  { no: "ATM-0204", name: "Kabir Malhotra", designation: "Enterprise AE", dept: SALES, location: "Delhi", employment: "full_time", mode: "hybrid", joined: "2021-08-30", status: "on_notice" },
  { no: "ATM-0205", name: "Emily Carter", designation: "Solutions Consultant", dept: SALES, location: "Mumbai", employment: "part_time", mode: "remote", joined: "2023-12-01", status: "active" },
  { no: "ATM-0301", name: "Sneha Kulkarni", designation: "HR Business Partner", dept: HR, location: "Pune", employment: "full_time", mode: "office", joined: "2020-10-05", status: "active" },
  { no: "ATM-0302", name: "Tobias Berg", designation: "Talent Acquisition Lead", dept: HR, location: "Bengaluru", employment: "full_time", mode: "hybrid", joined: "2022-02-14", status: "active" },
  { no: "ATM-0303", name: "Fatima Sheikh", designation: "People Ops Associate", dept: HR, location: "Hyderabad", employment: "full_time", mode: "office", joined: "2024-01-08", status: "probation" },
  { no: "ATM-0401", name: "Rahul Deshpande", designation: "Finance Controller", dept: FINANCE, location: "Mumbai", employment: "full_time", mode: "office", joined: "2019-11-22", status: "active" },
  { no: "ATM-0402", name: "Lena Novak", designation: "Financial Analyst", dept: FINANCE, location: "Pune", employment: "full_time", mode: "hybrid", joined: "2023-03-27", status: "active" },
  { no: "ATM-0403", name: "Aditya Ghosh", designation: "Payroll Specialist", dept: FINANCE, location: "Kolkata", employment: "full_time", mode: "office", joined: "2022-06-18", status: "active" },
  { no: "ATM-0404", name: "Nisha Rao", designation: "Accounts Payable Lead", dept: FINANCE, location: "Bengaluru", employment: "contract", mode: "remote", joined: "2024-07-01", status: "probation" },
  { no: "ATM-0501", name: "Marcus Feng", designation: "Operations Director", dept: OPERATIONS, location: "Bengaluru", employment: "full_time", mode: "office", joined: "2018-09-03", status: "active" },
  { no: "ATM-0502", name: "Ishita Banerjee", designation: "Ops Program Manager", dept: OPERATIONS, location: "Chennai", employment: "full_time", mode: "hybrid", joined: "2021-05-19", status: "active" },
  { no: "ATM-0503", name: "Omar Haddad", designation: "Facilities Coordinator", dept: OPERATIONS, location: "Hyderabad", employment: "full_time", mode: "field", joined: "2023-10-24", status: "active" },
  { no: "ATM-0504", name: "Divya Menon", designation: "Logistics Analyst", dept: OPERATIONS, location: "Chennai", employment: "part_time", mode: "remote", joined: "2024-04-16", status: "active" },
  { no: "ATM-0505", name: "Harpreet Kaur", designation: "Vendor Manager", dept: OPERATIONS, location: "Delhi", employment: "full_time", mode: "hybrid", joined: "2022-08-09", status: "on_notice" },
  { no: "ATM-0106b", name: "Lucas Almeida", designation: "Mobile Engineer", dept: ENGINEERING, location: "Pune", employment: "intern", mode: "remote", joined: "2025-06-02", status: "probation" },
];

const uuid = (n: number): string =>
  `e11a0000-0000-4000-8000-${n.toString().padStart(12, "0")}`;

export const EMPLOYEES: EmployeeOut[] = SEEDS.map((s, i) => ({
  id: uuid(i + 1),
  membership_id: uuid(1000 + i + 1),
  employee_no: s.no,
  full_name: s.name,
  designation: s.designation,
  // The demo seed carries the department name in `job_title`; `department_id` is unseeded.
  job_title: s.dept,
  department_id: null,
  location_code: s.location,
  employment_type: s.employment,
  work_mode: s.mode,
  date_of_joining: s.joined,
  // First member of each department reports to no one; the rest to that head.
  reporting_manager_id: null,
  dotted_line_manager_id: null,
  status: s.status,
  date_of_exit: null,
  created_at: `${s.joined}T09:00:00Z`,
  hierarchy_version: 1,
  pii: null,
}));

/* --------------------------- attendance for today --------------------------- */

const TODAY = "2026-08-24";

/** Slice 4: a due time is read against *now*, so the handful of fixtures that demonstrate one
 *  compute it off the real clock rather than off `TODAY` (a fixed past date) — otherwise "Due in
 *  6 hours" would render as a permanently stale "Overdue by …" no matter when this app runs.
 *
 *  The half-hour offsets are deliberate: a duration is floored to whole hours, so a deadline
 *  exactly six hours out reads "Due in 5 hours" by the time the row paints. Six and a half reads
 *  as the six the surrounding comments describe, whenever the demo is run. */
function hoursFromNow(hours: number): string {
  return new Date(Date.now() + hours * 60 * 60 * 1000).toISOString();
}

/** The fixture identity `AuthProvider` signs in as (`sneha.kulkarni@opsgrids.example.com`,
 *  ATM-0301). Every "own record" and "raised by me" fixture below is told from her seat. */
export const FIXTURE_EMPLOYEE_ID = uuid(12);

/** Mirrors `domain.ALLOWED_TRANSITIONS` (`services/attendance/app/domain.py`) for fixture
 *  generation only — never consulted by a live record, which always reads the server's own
 *  `allowed_transitions[]` (screens spec §4.C, C3). */
export const FIXTURE_ALLOWED_TRANSITIONS: Record<AttendanceStatus, AttendanceStatus[]> = {
  present: ["half_day", "absent", "regularized"],
  absent: ["present", "half_day", "regularized"],
  half_day: ["present", "absent", "regularized"],
  regularized: [],
};

const reqUuid = (n: number): string =>
  `f22b0000-0000-4000-9000-${n.toString().padStart(12, "0")}`;

export const REQ_ANANYA_ID = reqUuid(1);
export const REQ_GRACE_ID = reqUuid(2);
const REQ_MEERA_ID = reqUuid(3);
const REQ_ARJUN_ID = reqUuid(4);
const REQ_VIKRAM_ID = reqUuid(5);
export const REQ_EMILY_ID = reqUuid(6);
// Slice 4 — escalation (§2.3, §2.11): `escalated_to_me` and `escalated_away` cannot occur before
// a policy sweep exists, so no earlier slice needed a fixture for either.
export const REQ_ROHAN_ESCALATED_ID = reqUuid(7);
export const REQ_DANIEL_ESCALATED_AWAY_ID = reqUuid(8);
// Slice 4 — the inbox's overdue row (§4.A's Due column and `Overdue` filter). Assigned, so it has
// an approver to hold the deadline, and past it, so the fixture demo has something for the filter
// to find and for the `text-negative` treatment to render.
export const REQ_KABIR_OVERDUE_ID = reqUuid(9);
const REQ_MINE_1 = reqUuid(11);
const REQ_MINE_2 = reqUuid(12);
const REQ_MINE_3 = reqUuid(13);
const REQ_MINE_4 = reqUuid(14);
const REQ_MINE_5 = reqUuid(15);
const REQ_MINE_6 = reqUuid(16);

function pad(n: number): string {
  return n.toString().padStart(2, "0");
}

/** A regularization row exactly shaped like `RegularizationRequestOut` — every field the route
 *  now composes: the routed approver's name, the decider's name and comment, and the requester's
 *  own reason (attendance's own source, §4.C, §9.1 C3). */
function regularization(
  emp: EmployeeOut,
  overrides: Partial<
    Pick<
      NonNullable<AttendanceRecord["regularization"]>,
      | "id"
      | "requested_status"
      | "reason"
      | "status"
      | "approval_request_id"
      | "approval_opened_at"
      | "approver_employee_id"
      | "approver_name"
      | "decided_at"
      | "decision_comment"
      | "decided_by_name"
      | "failure_reason"
      | "approver_unassigned"
    >
  >,
): NonNullable<AttendanceRecord["regularization"]> {
  return {
    id: overrides.id ?? `${emp.id}-reg`,
    attendance_id: `att-${emp.id}`,
    employee_id: emp.id,
    requested_status: "regularized",
    reason: "See the request for the full reason.",
    status: "pending",
    approval_request_id: null,
    approval_opened_at: null,
    approver_employee_id: null,
    approver_name: null,
    decided_at: null,
    decision_comment: null,
    decided_by_name: null,
    failure_reason: null,
    created_at: `${TODAY}T09:30:00Z`,
    approver_unassigned: null,
    location_code: emp.location_code,
    ...overrides,
  };
}

// Deterministic per-employee attendance so the table and the stats always agree.
function attendanceFor(emp: EmployeeOut, index: number): AttendanceRecord {
  // A stable spread: most present, a few half days, a couple absent (on leave / no punch).
  const bucket = index % 10;
  let status: AttendanceStatus;
  if (bucket === 3 || bucket === 8) status = "absent";
  else if (bucket === 1 || bucket === 6) status = "half_day";
  else status = "present";

  // §types.ts: `source` is the wire's `web | mobile | biometric` — a field punch is a `work_mode`,
  // never a source. The fixture still varies it, on the employee's own work mode.
  const source: PunchSource =
    emp.work_mode === "remote" ? "mobile" : emp.work_mode === "field" ? "mobile" : "web";

  const base: AttendanceRecord = {
    id: `att-${emp.id}`,
    employee_id: emp.id,
    employee_no: emp.employee_no,
    full_name: emp.full_name,
    department_id: emp.department_id,
    job_title: emp.job_title,
    work_date: TODAY,
    check_in: null,
    check_out: null,
    worked_minutes: null,
    source,
    work_mode: emp.work_mode,
    status,
    location_code: emp.location_code,
    regularization: null,
    allowed_transitions: FIXTURE_ALLOWED_TRANSITIONS[status],
  };

  if (status !== "absent") {
    const inHour = status === "half_day" ? 10 : 9;
    const inMin = status === "half_day" ? 24 + ((index * 7) % 30) : (index * 5) % 55;
    const checkIn = `${TODAY}T${pad(inHour)}:${pad(inMin)}:00Z`;
    const outHour = 18 + (index % 2);
    const outMin = (index * 11) % 55;
    const checkOut = `${TODAY}T${pad(outHour)}:${pad(outMin)}:00Z`;
    const worked = outHour * 60 + outMin - (inHour * 60 + inMin);
    base.check_in = checkIn;
    base.check_out = checkOut;
    base.worked_minutes = worked;
  }

  return base;
}

const ATTENDANCE_BASE: AttendanceRecord[] = EMPLOYEES.map((e, i) => attendanceFor(e, i));

/** Looked up by employee number for the routed-approver / decider fixtures below — `byNo`
 *  (the approvals section's own helper) is declared later in the module, after this map. */
function employeeByNo(no: string): EmployeeOut {
  const emp = EMPLOYEES.find((e) => e.employee_no === no);
  if (!emp) throw new Error(`Fixture data error: no employee seeded with ${no}`);
  return emp;
}

/** Every "Correction" column state the screens spec asks for (§4.C), demonstrated on one
 *  employee's record each, so the whole app renders every state with no login. Only one row
 *  (the fixture viewer's own — ATM-0301, Sneha Kulkarni) is eligible for the "Request a
 *  correction" button; the rest show the states a colleague or HR would see on someone else's
 *  row, which the granted `regularization` object is readable on regardless of ownership. */
export const ATTENDANCE_TODAY: AttendanceRecord[] = ATTENDANCE_BASE.map((record) => {
  const emp = EMPLOYEES.find((e) => e.id === record.employee_id);
  if (!emp) return record;

  switch (emp.employee_no) {
    case "ATM-0101": // Ananya Iyer — routed, but the chain came up empty (matches
      // `FIXTURE_DETAIL_BY_ID[REQ_ANANYA_ID]`'s `inbox_reason: "unassigned"` on the approval side).
      return {
        ...record,
        status: "absent",
        allowed_transitions: FIXTURE_ALLOWED_TRANSITIONS.absent,
        regularization: regularization(emp, {
          id: `${emp.id}-reg`,
          requested_status: "regularized",
          status: "pending",
          approval_request_id: REQ_ANANYA_ID,
          approval_opened_at: `${TODAY}T07:12:00Z`,
          approver_unassigned: true,
          reason: "Worked from the Pune office, forgot to punch in.",
        }),
      };
    case "ATM-0104": // Daniel Okafor — the approval worker hasn't routed this one yet (§3.1, O6).
      return {
        ...record,
        status: "absent",
        allowed_transitions: FIXTURE_ALLOWED_TRANSITIONS.absent,
        regularization: regularization(emp, {
          requested_status: "present",
          status: "pending",
          approval_request_id: null,
          reason: "Was onsite at the client office all day.",
        }),
      };
    case "ATM-0105": // Meera Krishnan — approved, and applied: the record itself is regularised.
      return {
        ...record,
        status: "regularized",
        allowed_transitions: FIXTURE_ALLOWED_TRANSITIONS.regularized,
        regularization: regularization(emp, {
          requested_status: "regularized",
          status: "approved",
          approval_request_id: REQ_MEERA_ID,
          approval_opened_at: `${TODAY}T09:35:00Z`,
          approver_employee_id: employeeByNo("ATM-0103").id,
          approver_name: employeeByNo("ATM-0103").full_name,
          approver_unassigned: false,
          decided_at: `${TODAY}T11:05:00Z`,
          decided_by_name: employeeByNo("ATM-0103").full_name,
          reason: "On approved leave, forgot to raise it beforehand.",
        }),
      };
    case "ATM-0106": // Arjun Reddy — approved, but HR had already moved the day directly, so
      // the consumer's own transition re-check failed it (`services.apply_regularization_decision`
      // sets `status: "failed"` here, never `"approved"` — the record itself is left untouched).
      return {
        ...record,
        status: "absent",
        allowed_transitions: FIXTURE_ALLOWED_TRANSITIONS.absent,
        regularization: regularization(emp, {
          requested_status: "present",
          status: "failed",
          approval_request_id: REQ_ARJUN_ID,
          approval_opened_at: `${TODAY}T09:40:00Z`,
          approver_employee_id: employeeByNo("ATM-0501").id,
          approver_name: employeeByNo("ATM-0501").full_name,
          approver_unassigned: false,
          decided_at: `${TODAY}T12:40:00Z`,
          decided_by_name: employeeByNo("ATM-0501").full_name,
          failure_reason:
            "The record has since moved to 'absent'; a correction to 'present' is no longer legal.",
          reason: "Card reader was down at the gate all morning.",
        }),
      };
    case "ATM-0202": // Vikram Singh — rejected, with the decider's name and comment (design QA gap).
      return {
        ...record,
        status: "absent",
        allowed_transitions: FIXTURE_ALLOWED_TRANSITIONS.absent,
        regularization: regularization(emp, {
          requested_status: "present",
          status: "rejected",
          approval_request_id: REQ_VIKRAM_ID,
          approval_opened_at: `${TODAY}T09:10:00Z`,
          approver_employee_id: employeeByNo("ATM-0204").id,
          approver_name: employeeByNo("ATM-0204").full_name,
          approver_unassigned: false,
          decided_at: `${TODAY}T14:15:00Z`,
          decided_by_name: employeeByNo("ATM-0204").full_name,
          decision_comment: "No client visit logged for this date — please raise it with a manager note next time.",
          reason: "Client visit ran the whole day, no punch.",
        }),
      };
    case "ATM-0203": // Grace Adeyemi — a second pending, unassigned correction for the inbox.
      return {
        ...record,
        status: "present",
        allowed_transitions: FIXTURE_ALLOWED_TRANSITIONS.present,
        regularization: regularization(emp, {
          requested_status: "half_day",
          status: "pending",
          approval_request_id: REQ_GRACE_ID,
          approval_opened_at: `${TODAY}T09:07:00Z`,
          approver_unassigned: true,
          reason: "Left early for a family emergency; didn't get approval beforehand.",
        }),
      };
    case "ATM-0205": // Emily Carter — pending, routed and waiting on the fixture viewer by name
      // (§4.C: "Correction pending" · "Waiting on Sneha Kulkarni").
      return {
        ...record,
        status: "half_day",
        allowed_transitions: FIXTURE_ALLOWED_TRANSITIONS.half_day,
        regularization: regularization(emp, {
          requested_status: "present",
          status: "pending",
          approval_opened_at: `${TODAY}T12:52:00Z`,
          approver_employee_id: employeeByNo("ATM-0301").id,
          approver_name: employeeByNo("ATM-0301").full_name,
          approver_unassigned: false,
          approval_request_id: REQ_EMILY_ID,
          reason: "I was full day at the client site; my manager can confirm.",
        }),
      };
    case "ATM-0301": // Sneha Kulkarni — the fixture viewer's own record: no correction yet.
      return {
        ...record,
        status: "absent",
        allowed_transitions: FIXTURE_ALLOWED_TRANSITIONS.absent,
        regularization: null,
      };
    default:
      return record;
  }
});

/* --------------------------- locations & organization (zone lookup) --------------------------- */

const LOCATION_CODES = [...new Set(EMPLOYEES.map((e) => e.location_code).filter(Boolean))] as string[];

/** Every fixture city sits in India, so one IANA zone covers the whole set. */
export const FIXTURE_LOCATIONS: LocationOut[] = LOCATION_CODES.map((code, i) => ({
  id: uuid(2000 + i + 1),
  name: code,
  code,
  timezone: "Asia/Kolkata",
  country: "IN",
  is_active: true,
}));

export const FIXTURE_ORGANIZATION: OrganizationOut = {
  id: "org-fixture-0001",
  name: "OpsGrids India",
  timezone: "Asia/Kolkata",
};

/* --------------------------- approvals --------------------------- */

const byNo = (no: string): EmployeeOut => {
  const emp = EMPLOYEES.find((e) => e.employee_no === no);
  if (!emp) throw new Error(`Fixture data error: no employee seeded with ${no}`);
  return emp;
};

const ANANYA = byNo("ATM-0101");
const PRIYA = byNo("ATM-0103");
const MARCUS = byNo("ATM-0501");
const KABIR = byNo("ATM-0204");
const ROHAN = byNo("ATM-0102");
const GRACE = byNo("ATM-0203");
const EMILY = byNo("ATM-0205");
const SNEHA = byNo("ATM-0301");
const DANIEL = byNo("ATM-0104");

/**
 * Slice 4 (§4.A): every inbox row now carries the server's `due_at`, and the set below is chosen
 * so the Due column's three renderings and the `Overdue` filter all have a row in fixture mode —
 * a deadline ahead, a deadline behind, and two legitimate `null`s. The values match the same
 * request's `FIXTURE_DETAIL_BY_ID` step exactly: the inbox reports the deadline of the step the
 * reader is standing in front of, which for an escalated row is the **successor's**.
 */
export const FIXTURE_INBOX: InboxItemOut[] = [
  {
    id: REQ_ANANYA_ID,
    request_type: "hrms.time.regularization",
    source_ref: `att-${ANANYA.id}`,
    summary: { date: TODAY, from: "absent", to: "regularized" },
    request_note: "Worked from the Pune office, forgot to punch in.",
    opened_at: `${TODAY}T07:10:00Z`,
    requester_employee_id: ANANYA.id,
    requester_name: ANANYA.full_name,
    requester_employee_no: ANANYA.employee_no,
    requester_location_code: ANANYA.location_code,
    inbox_reason: "unassigned",
    from_name: null,
    // An unassigned step has nobody to hold a deadline, so the server stamps none
    // (`ck_approval_steps_unassigned_has_no_due`). The cell reads "No due time".
    due_at: null,
  },
  {
    id: REQ_GRACE_ID,
    request_type: "hrms.time.regularization",
    source_ref: `att-${GRACE.id}`,
    summary: { date: TODAY, from: "present", to: "half_day" },
    request_note: "Left early for a family emergency; didn't get approval beforehand.",
    opened_at: `${TODAY}T09:05:00Z`,
    requester_employee_id: GRACE.id,
    requester_name: GRACE.full_name,
    requester_employee_no: GRACE.employee_no,
    requester_location_code: GRACE.location_code,
    inbox_reason: "unassigned",
    from_name: null,
    due_at: null,
  },
  {
    id: REQ_EMILY_ID,
    request_type: "hrms.time.regularization",
    source_ref: `att-${EMILY.id}`,
    summary: { date: TODAY, from: "half_day", to: "present" },
    request_note: "I was full day at the client site; my manager can confirm.",
    opened_at: `${TODAY}T12:50:00Z`,
    requester_employee_id: EMILY.id,
    requester_name: EMILY.full_name,
    requester_employee_no: EMILY.employee_no,
    requester_location_code: EMILY.location_code,
    inbox_reason: "assigned",
    from_name: null,
    // The same instant as this request's open step in `FIXTURE_DETAIL_BY_ID` — "Due in 6 hours".
    due_at: hoursFromNow(6.5),
  },
  {
    // The overdue row: assigned to the fixture viewer, five hours past its deadline.
    id: REQ_KABIR_OVERDUE_ID,
    request_type: "hrms.time.regularization",
    source_ref: `att-${KABIR.id}`,
    summary: { date: TODAY, from: "absent", to: "present" },
    request_note: "Was at the Chennai site all day; the badge reader there isn't linked yet.",
    opened_at: `${TODAY}T04:20:00Z`,
    requester_employee_id: KABIR.id,
    requester_name: KABIR.full_name,
    requester_employee_no: KABIR.employee_no,
    requester_location_code: KABIR.location_code,
    inbox_reason: "assigned",
    from_name: null,
    due_at: hoursFromNow(-5.5),
  },
  // Slice 4 (§2.3): the two escalation reasons, each with the `from_name` the chip names —
  // `escalated_to_me` ("moved up to you because {from_name} didn't answer") and `escalated_away`
  // ("still yours — now with {from_name}", the fixture viewer's own step that escalated past her).
  {
    id: REQ_ROHAN_ESCALATED_ID,
    request_type: "hrms.time.regularization",
    source_ref: `att-${ROHAN.id}`,
    summary: { date: TODAY, from: "absent", to: "present" },
    request_note: "Client visit ran past midnight; punched in from the hotel the next morning.",
    opened_at: `${TODAY}T02:00:00Z`,
    requester_employee_id: ROHAN.id,
    requester_name: ROHAN.full_name,
    requester_employee_no: ROHAN.employee_no,
    requester_location_code: ROHAN.location_code,
    inbox_reason: "escalated_to_me",
    from_name: PRIYA.full_name,
    // The **successor** step's deadline — Sneha's own, four hours out — not Priya's closed one,
    // which stopped being anyone's deadline the moment it escalated.
    due_at: hoursFromNow(4.5),
  },
  {
    id: REQ_DANIEL_ESCALATED_AWAY_ID,
    request_type: "hrms.time.regularization",
    source_ref: `att-${DANIEL.id}`,
    summary: { date: TODAY, from: "present", to: "half_day" },
    request_note: "Doctor's appointment ran long; left before the second half of the day.",
    opened_at: `${TODAY}T01:30:00Z`,
    requester_employee_id: DANIEL.id,
    requester_name: DANIEL.full_name,
    requester_employee_no: DANIEL.employee_no,
    requester_location_code: DANIEL.location_code,
    inbox_reason: "escalated_away",
    from_name: MARCUS.full_name,
    // Escalated past the viewer to Marcus: the deadline shown is his open step's, an hour out.
    due_at: hoursFromNow(1.5),
  },
];

export const FIXTURE_MY_REQUESTS: RequestListItemOut[] = [
  {
    id: REQ_MINE_1,
    request_type: "hrms.time.regularization",
    source_ref: `att-${SNEHA.id}-1`,
    summary: { date: "2026-08-22", from: "absent", to: "present" },
    status: "pending",
    opened_at: "2026-08-22T08:15:00Z",
    requester_employee_id: SNEHA.id,
    requester_name: SNEHA.full_name,
    requester_employee_no: SNEHA.employee_no,
    requester_location_code: SNEHA.location_code,
    approver_employee_id: PRIYA.id,
    approver_name: PRIYA.full_name,
    decided_by_employee_id: null,
    decided_by_name: null,
    decided_at: null,
    comment: null,
    application_state: null,
    application_failure_reason: null,
  },
  {
    id: REQ_MINE_2,
    request_type: "hrms.time.regularization",
    source_ref: `att-${SNEHA.id}-2`,
    summary: { date: "2026-08-20", from: "present", to: "half_day" },
    status: "pending",
    opened_at: "2026-08-20T10:00:00Z",
    requester_employee_id: SNEHA.id,
    requester_name: SNEHA.full_name,
    requester_employee_no: SNEHA.employee_no,
    requester_location_code: SNEHA.location_code,
    approver_employee_id: null,
    approver_name: null,
    decided_by_employee_id: null,
    decided_by_name: null,
    decided_at: null,
    comment: null,
    application_state: null,
    application_failure_reason: null,
  },
  {
    id: REQ_MINE_3,
    request_type: "hrms.time.regularization",
    source_ref: `att-${SNEHA.id}-3`,
    summary: { date: "2026-08-18", from: "absent", to: "regularized" },
    status: "approved",
    opened_at: "2026-08-18T08:00:00Z",
    requester_employee_id: SNEHA.id,
    requester_name: SNEHA.full_name,
    requester_employee_no: SNEHA.employee_no,
    requester_location_code: SNEHA.location_code,
    approver_employee_id: null,
    approver_name: null,
    decided_by_employee_id: MARCUS.id,
    decided_by_name: MARCUS.full_name,
    decided_at: "2026-08-18T15:40:00Z",
    comment: "Approved, thanks for flagging it.",
    application_state: "applied",
    application_failure_reason: null,
  },
  {
    id: REQ_MINE_4,
    request_type: "hrms.time.regularization",
    source_ref: `att-${SNEHA.id}-4`,
    summary: { date: "2026-08-15", from: "absent", to: "present" },
    status: "approved",
    opened_at: "2026-08-15T08:00:00Z",
    requester_employee_id: SNEHA.id,
    requester_name: SNEHA.full_name,
    requester_employee_no: SNEHA.employee_no,
    requester_location_code: SNEHA.location_code,
    approver_employee_id: null,
    approver_name: null,
    decided_by_employee_id: MARCUS.id,
    decided_by_name: MARCUS.full_name,
    decided_at: "2026-08-15T16:10:00Z",
    comment: null,
    application_state: "failed",
    application_failure_reason:
      "The record has since moved to 'half_day'; a correction to 'present' is no longer legal.",
  },
  {
    id: REQ_MINE_5,
    request_type: "hrms.time.regularization",
    source_ref: `att-${SNEHA.id}-5`,
    summary: { date: "2026-08-12", from: "present", to: "half_day" },
    status: "rejected",
    opened_at: "2026-08-12T08:00:00Z",
    requester_employee_id: SNEHA.id,
    requester_name: SNEHA.full_name,
    requester_employee_no: SNEHA.employee_no,
    requester_location_code: SNEHA.location_code,
    approver_employee_id: null,
    approver_name: null,
    decided_by_employee_id: PRIYA.id,
    decided_by_name: PRIYA.full_name,
    decided_at: "2026-08-12T13:20:00Z",
    comment: "You'd already logged this as leave — no change needed.",
    application_state: "applied",
    application_failure_reason: null,
  },
  {
    // Illustrative only: today's `apply_regularization_decision` never fails a rejection (it sets
    // `regularization_requests.status = "rejected"` unconditionally), so this combination cannot
    // currently occur end-to-end. Kept as a fixture anyway because the response shape allows it
    // and the screens spec requires the state to be built (§2.10) — see the slice-2 report.
    id: REQ_MINE_6,
    request_type: "hrms.time.regularization",
    source_ref: `att-${SNEHA.id}-6`,
    summary: { date: "2026-08-10", from: "absent", to: "present" },
    status: "rejected",
    opened_at: "2026-08-10T08:00:00Z",
    requester_employee_id: SNEHA.id,
    requester_name: SNEHA.full_name,
    requester_employee_no: SNEHA.employee_no,
    requester_location_code: SNEHA.location_code,
    approver_employee_id: null,
    approver_name: null,
    decided_by_employee_id: PRIYA.id,
    decided_by_name: PRIYA.full_name,
    decided_at: "2026-08-10T09:45:00Z",
    comment: "Already covered by yesterday's correction.",
    application_state: "failed",
    application_failure_reason: "The attendance record could not be updated to reflect the rejection.",
  },
];

function stepsFor(item: {
  requester: EmployeeOut;
  approver: EmployeeOut | null;
  openedAt: string;
  decidedAt?: string;
  outcome?: "approved" | "rejected";
  /** Slice 4 (§2.11): `null` for an unassigned step, always — the server never stamps a deadline
   *  with nobody to hold it. Only ever set here for an *assigned* step. */
  dueAt?: string | null;
}): StepOut[] {
  const opened: StepOut = {
    level: 1,
    reason: item.approver ? "assigned" : "unassigned",
    outcome: item.decidedAt ? (item.outcome ?? "approved") : "open",
    approver_employee_id: item.approver?.id ?? null,
    approver_name: item.approver?.full_name ?? null,
    acting_for_employee_id: null,
    acting_for_name: null,
    opened_at: item.openedAt,
    closed_at: item.decidedAt ?? null,
    due_at: item.approver ? (item.dueAt ?? null) : null,
  };
  return [opened];
}

export const FIXTURE_DETAIL_BY_ID: Record<string, RequestDetailOut> = {
  [REQ_ANANYA_ID]: {
    id: REQ_ANANYA_ID,
    request_type: "hrms.time.regularization",
    source_ref: `att-${ANANYA.id}`,
    summary: { date: TODAY, from: "absent", to: "regularized" },
    request_note: "Worked from the Pune office, forgot to punch in.",
    status: "pending",
    opened_at: `${TODAY}T07:10:00Z`,
    requester_employee_id: ANANYA.id,
    requester_name: ANANYA.full_name,
    requester_employee_no: ANANYA.employee_no,
    requester_location_code: ANANYA.location_code,
    approver_employee_id: null,
    approver_name: null,
    acting_for_employee_id: null,
    acting_for_name: null,
    inbox_reason: "unassigned",
    from_name: null,
    decided_by_employee_id: null,
    decided_by_name: null,
    decided_at: null,
    comment: null,
    application_state: null,
    application_failure_reason: null,
    applied_at: null,
    steps: stepsFor({ requester: ANANYA, approver: null, openedAt: `${TODAY}T07:10:00Z` }),
  },
  [REQ_GRACE_ID]: {
    id: REQ_GRACE_ID,
    request_type: "hrms.time.regularization",
    source_ref: `att-${GRACE.id}`,
    summary: { date: TODAY, from: "present", to: "half_day" },
    request_note: "Left early for a family emergency; didn't get approval beforehand.",
    status: "pending",
    opened_at: `${TODAY}T09:05:00Z`,
    requester_employee_id: GRACE.id,
    requester_name: GRACE.full_name,
    requester_employee_no: GRACE.employee_no,
    requester_location_code: GRACE.location_code,
    approver_employee_id: null,
    approver_name: null,
    acting_for_employee_id: null,
    acting_for_name: null,
    inbox_reason: "unassigned",
    from_name: null,
    decided_by_employee_id: null,
    decided_by_name: null,
    decided_at: null,
    comment: null,
    application_state: null,
    application_failure_reason: null,
    applied_at: null,
    steps: stepsFor({ requester: GRACE, approver: null, openedAt: `${TODAY}T09:05:00Z` }),
  },
  [REQ_EMILY_ID]: {
    id: REQ_EMILY_ID,
    request_type: "hrms.time.regularization",
    source_ref: `att-${EMILY.id}`,
    summary: { date: TODAY, from: "half_day", to: "present" },
    request_note: "I was full day at the client site; my manager can confirm.",
    status: "pending",
    opened_at: `${TODAY}T12:50:00Z`,
    requester_employee_id: EMILY.id,
    requester_name: EMILY.full_name,
    requester_employee_no: EMILY.employee_no,
    requester_location_code: EMILY.location_code,
    approver_employee_id: SNEHA.id,
    approver_name: SNEHA.full_name,
    acting_for_employee_id: null,
    acting_for_name: null,
    inbox_reason: "assigned",
    from_name: null,
    decided_by_employee_id: null,
    decided_by_name: null,
    decided_at: null,
    comment: null,
    application_state: null,
    application_failure_reason: null,
    applied_at: null,
    steps: stepsFor({
      requester: EMILY,
      approver: SNEHA,
      openedAt: `${TODAY}T12:50:00Z`,
      // Slice 4: computed off the real clock, not `TODAY` (a fixed past date every other
      // timestamp here uses) — a due time is meant to be read against *now*, so this is the one
      // fixture field that has to track it to demonstrate "Due in …" rather than a permanently
      // stale "Overdue by …".
      dueAt: hoursFromNow(6.5),
    }),
  },
  [REQ_KABIR_OVERDUE_ID]: {
    id: REQ_KABIR_OVERDUE_ID,
    request_type: "hrms.time.regularization",
    source_ref: `att-${KABIR.id}`,
    summary: { date: TODAY, from: "absent", to: "present" },
    request_note: "Was at the Chennai site all day; the badge reader there isn't linked yet.",
    status: "pending",
    opened_at: `${TODAY}T04:20:00Z`,
    requester_employee_id: KABIR.id,
    requester_name: KABIR.full_name,
    requester_employee_no: KABIR.employee_no,
    requester_location_code: KABIR.location_code,
    approver_employee_id: SNEHA.id,
    approver_name: SNEHA.full_name,
    acting_for_employee_id: null,
    acting_for_name: null,
    inbox_reason: "assigned",
    from_name: null,
    decided_by_employee_id: null,
    decided_by_name: null,
    decided_at: null,
    comment: null,
    application_state: null,
    application_failure_reason: null,
    applied_at: null,
    // Past its deadline and still open: the sweep escalates on its next pass, which is exactly
    // what the inbox's overdue treatment is warning the approver about.
    steps: stepsFor({
      requester: KABIR,
      approver: SNEHA,
      openedAt: `${TODAY}T04:20:00Z`,
      dueAt: hoursFromNow(-5.5),
    }),
  },
  [REQ_ROHAN_ESCALATED_ID]: {
    id: REQ_ROHAN_ESCALATED_ID,
    request_type: "hrms.time.regularization",
    source_ref: `att-${ROHAN.id}`,
    summary: { date: TODAY, from: "absent", to: "present" },
    request_note: "Client visit ran past midnight; punched in from the hotel the next morning.",
    status: "pending",
    opened_at: `${TODAY}T02:00:00Z`,
    requester_employee_id: ROHAN.id,
    requester_name: ROHAN.full_name,
    requester_employee_no: ROHAN.employee_no,
    requester_location_code: ROHAN.location_code,
    approver_employee_id: SNEHA.id,
    approver_name: SNEHA.full_name,
    acting_for_employee_id: null,
    acting_for_name: null,
    inbox_reason: "escalated_to_me",
    from_name: PRIYA.full_name,
    decided_by_employee_id: null,
    decided_by_name: null,
    decided_at: null,
    comment: null,
    application_state: null,
    application_failure_reason: null,
    applied_at: null,
    // Escalation gets a fresh due time on the rung it lands on (§3.5): Priya's own step closed
    // "escalated" a couple of hours ago; Sneha's step opened then, with a new deadline ahead.
    steps: [
      {
        level: 1,
        reason: "assigned",
        outcome: "escalated",
        approver_employee_id: PRIYA.id,
        approver_name: PRIYA.full_name,
        acting_for_employee_id: null,
        acting_for_name: null,
        opened_at: `${TODAY}T02:00:00Z`,
        closed_at: hoursFromNow(-2),
        due_at: hoursFromNow(-2),
      },
      {
        level: 2,
        reason: "escalated",
        outcome: "open",
        approver_employee_id: SNEHA.id,
        approver_name: SNEHA.full_name,
        acting_for_employee_id: null,
        acting_for_name: null,
        opened_at: hoursFromNow(-2),
        closed_at: null,
        due_at: hoursFromNow(4.5),
      },
    ],
  },
  [REQ_DANIEL_ESCALATED_AWAY_ID]: {
    id: REQ_DANIEL_ESCALATED_AWAY_ID,
    request_type: "hrms.time.regularization",
    source_ref: `att-${DANIEL.id}`,
    summary: { date: TODAY, from: "present", to: "half_day" },
    request_note: "Doctor's appointment ran long; left before the second half of the day.",
    status: "pending",
    opened_at: `${TODAY}T01:30:00Z`,
    requester_employee_id: DANIEL.id,
    requester_name: DANIEL.full_name,
    requester_employee_no: DANIEL.employee_no,
    requester_location_code: DANIEL.location_code,
    // Escalated past Sneha, who remains eligible (§2.3's "Still yours — now with {Name}") — the
    // detail's "Waiting on" card names the *current* approver, Marcus, not her.
    approver_employee_id: MARCUS.id,
    approver_name: MARCUS.full_name,
    acting_for_employee_id: null,
    acting_for_name: null,
    inbox_reason: "escalated_away",
    from_name: MARCUS.full_name,
    decided_by_employee_id: null,
    decided_by_name: null,
    decided_at: null,
    comment: null,
    application_state: null,
    application_failure_reason: null,
    applied_at: null,
    steps: [
      {
        level: 1,
        reason: "assigned",
        outcome: "escalated",
        approver_employee_id: SNEHA.id,
        approver_name: SNEHA.full_name,
        acting_for_employee_id: null,
        acting_for_name: null,
        opened_at: `${TODAY}T01:30:00Z`,
        closed_at: hoursFromNow(-3),
        due_at: hoursFromNow(-3),
      },
      {
        level: 2,
        reason: "escalated",
        outcome: "open",
        approver_employee_id: MARCUS.id,
        approver_name: MARCUS.full_name,
        acting_for_employee_id: null,
        acting_for_name: null,
        opened_at: hoursFromNow(-3),
        closed_at: null,
        due_at: hoursFromNow(1.5),
      },
    ],
  },
};

function myRequestDetail(item: RequestListItemOut): RequestDetailOut {
  const approver =
    item.decided_by_employee_id != null
      ? (EMPLOYEES.find((e) => e.id === item.decided_by_employee_id) ?? null)
      : item.approver_employee_id != null
        ? (EMPLOYEES.find((e) => e.id === item.approver_employee_id) ?? null)
        : null;
  const decidedOutcome: "approved" | "rejected" | undefined =
    item.status === "approved" || item.status === "rejected" ? item.status : undefined;

  return {
    id: item.id,
    request_type: item.request_type,
    source_ref: item.source_ref,
    summary: item.summary,
    request_note: null,
    status: item.status,
    opened_at: item.opened_at,
    requester_employee_id: item.requester_employee_id,
    requester_name: item.requester_name,
    requester_employee_no: item.requester_employee_no,
    requester_location_code: item.requester_location_code,
    approver_employee_id: item.approver_employee_id,
    approver_name: item.approver_name,
    acting_for_employee_id: null,
    acting_for_name: null,
    inbox_reason: null,
    from_name: null,
    decided_by_employee_id: item.decided_by_employee_id,
    decided_by_name: item.decided_by_name,
    decided_at: item.decided_at,
    comment: item.comment,
    application_state: item.application_state,
    application_failure_reason: item.application_failure_reason,
    applied_at: item.decided_at,
    steps: stepsFor({
      requester: SNEHA,
      approver,
      openedAt: item.opened_at,
      ...(item.decided_at ? { decidedAt: item.decided_at } : {}),
      ...(decidedOutcome ? { outcome: decidedOutcome } : {}),
    }),
  };
}

for (const item of FIXTURE_MY_REQUESTS) {
  FIXTURE_DETAIL_BY_ID[item.id] = myRequestDetail(item);
}

/** Org-wide view (`GET /approvals/requests` without `requester=me`) — every request known to the
 *  fixture set, including other people's, newest first. Demonstrates gap 10's "All requests" tab
 *  for a caller holding `core.approval.request.read:ORG`. */
export const FIXTURE_ORG_REQUESTS: RequestListItemOut[] = Object.values(FIXTURE_DETAIL_BY_ID)
  .map((d): RequestListItemOut => ({
    id: d.id,
    request_type: d.request_type,
    source_ref: d.source_ref,
    summary: d.summary,
    status: d.status,
    opened_at: d.opened_at,
    requester_employee_id: d.requester_employee_id,
    requester_name: d.requester_name,
    requester_employee_no: d.requester_employee_no,
    requester_location_code: d.requester_location_code,
    approver_employee_id: d.approver_employee_id,
    approver_name: d.approver_name,
    decided_by_employee_id: d.decided_by_employee_id,
    decided_by_name: d.decided_by_name,
    decided_at: d.decided_at,
    comment: d.comment,
    application_state: d.application_state,
    application_failure_reason: d.application_failure_reason,
  }))
  .sort((a, b) => new Date(b.opened_at).getTime() - new Date(a.opened_at).getTime());

export const FIXTURE_CHAIN: ApprovalChainOut = {
  employee_id: ANANYA.id,
  location_code: ANANYA.location_code,
  chain: [
    {
      level: 1,
      employee_id: PRIYA.id,
      display_name: PRIYA.full_name,
      employee_no: PRIYA.employee_no,
      skipped: false,
      is_fallback: false,
    },
    {
      level: 2,
      employee_id: KABIR.id,
      display_name: KABIR.full_name,
      employee_no: KABIR.employee_no,
      skipped: true,
      is_fallback: false,
    },
    {
      level: 3,
      employee_id: MARCUS.id,
      display_name: MARCUS.full_name,
      employee_no: MARCUS.employee_no,
      skipped: false,
      is_fallback: false,
    },
    {
      level: 4,
      employee_id: null,
      display_name: null,
      employee_no: null,
      skipped: false,
      is_fallback: true,
    },
  ],
  dotted_line_manager: {
    employee_id: ROHAN.id,
    display_name: ROHAN.full_name,
    employee_no: ROHAN.employee_no,
  },
  unresolved: null,
};

/* --------------------------- delegations (slice 3) --------------------------- */

const delUuid = (n: number): string => `a33c0000-0000-4000-a000-${n.toString().padStart(12, "0")}`;

/** Mirrors `domain.delegation_status` (`services/approval/app/domain.py`) — fixture-only, so a
 *  delegation the fixture viewer creates or revokes against real wall-clock dates still tags
 *  itself the same way the live service would. A live row never computes this client-side; its
 *  `status` always comes straight off the wire. */
function fixtureDelegationStatus(
  revokedAt: string | null,
  startsAt: string,
  endsAt: string,
  at: Date,
): DelegationOut["status"] {
  if (revokedAt !== null) return "revoked";
  const now = at.getTime();
  if (now < new Date(startsAt).getTime()) return "scheduled";
  if (now >= new Date(endsAt).getTime()) return "ended";
  return "active";
}

const DEL_ACTIVE_MINE_ID = delUuid(1);
const DEL_SCHEDULED_MINE_ID = delUuid(2);
const DEL_REVOKED_MINE_ID = delUuid(3);
const DEL_ENDED_MINE_ID = delUuid(4);
const DEL_DELEGATED_TO_ME_ID = delUuid(5);
const DEL_OTHERS_ID = delUuid(6);

/** Every status §4.E's three sections need, demonstrated from the fixture viewer's seat (Sneha
 *  Kulkarni, ATM-0301) — delegator on most rows, delegate on one (the "either party" reading), and
 *  one row that names neither, visible only in ORG mode. Dates are relative to *now* so the demo
 *  stays honest across however many days this fixture set has been running. */
// Typed here, ahead of `.sort()` below: a method call on an array literal is not itself
// contextually typed by the outer `const` annotation, so `status: "active"` would otherwise
// widen to `string` and fail `DelegationOut`'s literal union.
const UNSORTED_FIXTURE_DELEGATIONS: DelegationOut[] = [
  {
    id: DEL_ACTIVE_MINE_ID,
    delegator_employee_id: SNEHA.id,
    delegator_name: SNEHA.full_name,
    delegator_location_code: SNEHA.location_code,
    delegate_employee_id: ROHAN.id,
    delegate_name: ROHAN.full_name,
    starts_at: new Date(Date.now() - 2 * 24 * 60 * 60 * 1000).toISOString(),
    ends_at: new Date(Date.now() + 5 * 24 * 60 * 60 * 1000).toISOString(),
    status: "active",
    created_by_employee_id: SNEHA.id,
    revoked_at: null,
    revoked_by_employee_id: null,
  },
  {
    id: DEL_SCHEDULED_MINE_ID,
    delegator_employee_id: SNEHA.id,
    delegator_name: SNEHA.full_name,
    delegator_location_code: SNEHA.location_code,
    delegate_employee_id: DANIEL.id,
    delegate_name: DANIEL.full_name,
    starts_at: new Date(Date.now() + 10 * 24 * 60 * 60 * 1000).toISOString(),
    ends_at: new Date(Date.now() + 17 * 24 * 60 * 60 * 1000).toISOString(),
    status: "scheduled",
    created_by_employee_id: SNEHA.id,
    revoked_at: null,
    revoked_by_employee_id: null,
  },
  {
    id: DEL_ENDED_MINE_ID,
    delegator_employee_id: SNEHA.id,
    delegator_name: SNEHA.full_name,
    delegator_location_code: SNEHA.location_code,
    delegate_employee_id: ROHAN.id,
    delegate_name: ROHAN.full_name,
    starts_at: new Date(Date.now() - 30 * 24 * 60 * 60 * 1000).toISOString(),
    ends_at: new Date(Date.now() - 23 * 24 * 60 * 60 * 1000).toISOString(),
    status: "ended",
    created_by_employee_id: SNEHA.id,
    revoked_at: null,
    revoked_by_employee_id: null,
  },
  {
    id: DEL_REVOKED_MINE_ID,
    delegator_employee_id: SNEHA.id,
    delegator_name: SNEHA.full_name,
    delegator_location_code: SNEHA.location_code,
    delegate_employee_id: MARCUS.id,
    delegate_name: MARCUS.full_name,
    starts_at: new Date(Date.now() - 15 * 24 * 60 * 60 * 1000).toISOString(),
    ends_at: new Date(Date.now() + 15 * 24 * 60 * 60 * 1000).toISOString(),
    status: "revoked",
    created_by_employee_id: SNEHA.id,
    revoked_at: new Date(Date.now() - 10 * 24 * 60 * 60 * 1000).toISOString(),
    revoked_by_employee_id: SNEHA.id,
  },
  {
    id: DEL_DELEGATED_TO_ME_ID,
    delegator_employee_id: PRIYA.id,
    delegator_name: PRIYA.full_name,
    delegator_location_code: PRIYA.location_code,
    delegate_employee_id: SNEHA.id,
    delegate_name: SNEHA.full_name,
    starts_at: new Date(Date.now() - 1 * 24 * 60 * 60 * 1000).toISOString(),
    ends_at: new Date(Date.now() + 6 * 24 * 60 * 60 * 1000).toISOString(),
    status: "active",
    created_by_employee_id: PRIYA.id,
    revoked_at: null,
    revoked_by_employee_id: null,
  },
  {
    // Names neither party as the fixture viewer — visible only once `core.approval.delegation
    // .write:ORG` is granted (fixture mode grants it, §2.4's G1 fallback), demonstrating "ORG
    // sees every delegation in the organization" (routes.py `list_delegations`).
    id: DEL_OTHERS_ID,
    delegator_employee_id: MARCUS.id,
    delegator_name: MARCUS.full_name,
    delegator_location_code: MARCUS.location_code,
    delegate_employee_id: KABIR.id,
    delegate_name: KABIR.full_name,
    starts_at: new Date(Date.now() - 3 * 24 * 60 * 60 * 1000).toISOString(),
    ends_at: new Date(Date.now() + 4 * 24 * 60 * 60 * 1000).toISOString(),
    status: "active",
    created_by_employee_id: MARCUS.id,
    revoked_at: null,
    revoked_by_employee_id: null,
  },
];

export const FIXTURE_DELEGATIONS: DelegationOut[] = [...UNSORTED_FIXTURE_DELEGATIONS].sort(
  (a, b) => new Date(b.starts_at).getTime() - new Date(a.starts_at).getTime(),
);

/** The mutable, in-session copy of the fixture delegation list — `listDelegations` reads it,
 *  `createDelegation`/`revokeDelegation` write to it, the same "representative but internally
 *  consistent" contract `sampleRoster` (`api/client.ts`) keeps for attendance. */
let delegationStore: DelegationOut[] = FIXTURE_DELEGATIONS.map((d) => ({ ...d }));

/**
 * `employeeId` mirrors the endpoint's `employee_id` exactly: delegations the named person is a
 * party to in **either** direction, filtered before paging — so the fixture demo's `total` and
 * "Load more" behave the way the live screen's do, which is the whole point of replacing the old
 * client-side search. A person with no delegations returns an empty page, not every row.
 */
export function __listFixtureDelegations(employeeId: string | null = null): DelegationOut[] {
  return delegationStore
    .filter(
      (d) =>
        employeeId === null ||
        d.delegator_employee_id === employeeId ||
        d.delegate_employee_id === employeeId,
    )
    .map((d) => ({
      ...d,
      status: fixtureDelegationStatus(d.revoked_at, d.starts_at, d.ends_at, new Date()),
    }));
}

/** Mirrors `services.create_delegation`'s field-level refusals (self-delegation, an end before or
 *  in the past, an exited delegate, an overlap) so every failure branch in screens §3.3 is
 *  exercised without a login — the same discipline `client.requestRegularization` keeps for
 *  attendance's own rules. */
export function __createFixtureDelegation(body: {
  delegator_employee_id?: string;
  delegate_employee_id: string;
  starts_at: string;
  ends_at: string;
}): { delegation: DelegationOut; repointed_count: number } {
  const delegatorId = body.delegator_employee_id ?? SNEHA.id;
  const now = new Date();

  if (body.delegate_employee_id === delegatorId) {
    throw problemErrorFor(422, "You can't delegate to yourself", [
      {
        field: "delegate_employee_id",
        code: "self_delegation",
        message: "You can't delegate to yourself.",
      },
    ]);
  }
  const startsAt = new Date(body.starts_at);
  const endsAt = new Date(body.ends_at);
  if (endsAt.getTime() <= startsAt.getTime()) {
    throw problemErrorFor(422, "End must be after the start", [
      { field: "ends_at", code: "window_backwards", message: "End must be after the start." },
    ]);
  }
  if (endsAt.getTime() <= now.getTime()) {
    throw problemErrorFor(422, "Pick a time in the future", [
      { field: "ends_at", code: "window_in_the_past", message: "Pick a time in the future." },
    ]);
  }
  const delegateEmployee = EMPLOYEES.find((e) => e.id === body.delegate_employee_id);
  if (!delegateEmployee || delegateEmployee.status === "exited") {
    throw problemErrorFor(422, "That colleague can't be delegated to", [
      {
        field: "delegate_employee_id",
        code: "delegate_not_available",
        message: "That colleague can't be delegated to right now.",
      },
    ]);
  }
  const overlap = delegationStore.find(
    (d) =>
      d.delegator_employee_id === delegatorId &&
      d.revoked_at === null &&
      new Date(d.starts_at).getTime() < endsAt.getTime() &&
      new Date(d.ends_at).getTime() > startsAt.getTime(),
  );
  if (overlap) {
    throw problemErrorFor(
      409,
      `You already have a delegation from ${overlap.starts_at} to ${overlap.ends_at} that covers those dates.`,
    );
  }

  const delegatorEmployee = EMPLOYEES.find((e) => e.id === delegatorId) ?? SNEHA;
  const created: DelegationOut = {
    id: delUuid(1000 + delegationStore.length + 1),
    delegator_employee_id: delegatorId,
    delegator_name: delegatorEmployee.full_name,
    delegator_location_code: delegatorEmployee.location_code,
    delegate_employee_id: delegateEmployee.id,
    delegate_name: delegateEmployee.full_name,
    starts_at: body.starts_at,
    ends_at: body.ends_at,
    status: fixtureDelegationStatus(null, body.starts_at, body.ends_at, now),
    created_by_employee_id: SNEHA.id,
    revoked_at: null,
    revoked_by_employee_id: null,
  };
  delegationStore = [created, ...delegationStore];

  // Re-points only when the window has already opened (`services.create_delegation`, §5) — the
  // fixture inbox has exactly one open, unassigned step per requester and no step currently
  // assigned to a delegator other than Sneha, so the illustrative count is 1 exactly when this
  // delegation starts immediately and the delegator is the fixture viewer, 0 otherwise.
  const repointedCount = startsAt.getTime() <= now.getTime() && delegatorId === SNEHA.id ? 1 : 0;
  return { delegation: created, repointed_count: repointedCount };
}

export function __revokeFixtureDelegation(delegationId: string): DelegationOut {
  const existing = delegationStore.find((d) => d.id === delegationId);
  if (!existing) {
    throw problemErrorFor(404, "That delegation isn't available to you");
  }
  if (existing.revoked_at !== null) {
    throw problemErrorFor(409, "This delegation was already revoked");
  }
  const revoked: DelegationOut = {
    ...existing,
    status: "revoked",
    revoked_at: new Date().toISOString(),
    revoked_by_employee_id: SNEHA.id,
  };
  delegationStore = delegationStore.map((d) => (d.id === delegationId ? revoked : d));
  return revoked;
}

/** Test-only: restores the fixture delegation list to its initial state between test cases. */
export function __resetFixtureDelegationsForTests(): void {
  delegationStore = FIXTURE_DELEGATIONS.map((d) => ({ ...d }));
}

function problemErrorFor(
  status: number,
  detail: string,
  errors?: { field: string; code: string; message: string }[],
): ApiError {
  const body: ProblemJson & { errors?: typeof errors } = {
    detail,
    ...(errors ? { errors } : {}),
  };
  return new ApiError(status, detail, body);
}

/** The delegate picker's fixture source (`GET /delegate-candidates`): non-exited employees whose
 *  name or staff number **starts with** `q` (`selectors._DELEGATE_CANDIDATES_SQL`'s own match —
 *  not a substring search), excluding the fixture viewer, ordered by name, capped at `limit`. */
export function __searchFixtureDelegateCandidates(q: string, limit: number): DelegateCandidateOut[] {
  const prefix = q.trim().toLowerCase();
  return EMPLOYEES.filter(
    (e) =>
      e.id !== SNEHA.id &&
      e.status !== "exited" &&
      (e.full_name.toLowerCase().startsWith(prefix) ||
        e.employee_no.toLowerCase().startsWith(prefix)),
  )
    .sort((a, b) => a.full_name.localeCompare(b.full_name))
    .slice(0, limit)
    .map((e) => ({ employee_id: e.id, display_name: e.full_name, employee_no: e.employee_no }));
}

/* ----------------------------------------------------------- policy administration (slice 4) */

// Mirrors `services.KNOWN_REQUEST_TYPES` — today, exactly one. `GET /policies` iterates this
// same tuple server-side, which is why a type with nothing configured still appears (screens
// §4.G's "Using the default settings"), never as a fabricated row this fixture invents on its
// own that the real endpoint would not also produce.
const FIXTURE_POLICY_DEFAULTS: PolicyOut[] = [
  { request_type: "hrms.time.regularization", sla_minutes: 1440, enabled: true },
];

let policyStore: PolicyOut[] = FIXTURE_POLICY_DEFAULTS.map((p) => ({ ...p }));

export function __listFixturePolicies(): PolicyOut[] {
  return policyStore.map((p) => ({ ...p }));
}

/** Mirrors `services.update_policy` exactly: an unknown `request_type` is a field-level 422 on
 *  that field, and `sla_minutes` outside `15..525600` — `ApprovalPolicy`'s own CHECK — is a
 *  field-level 422 on `sla_minutes`, with pydantic's own `ge`/`le` message (verbatim, §2.5),
 *  never a friendlier fixture-only rewrite that the real service would not send. */
export function __updateFixturePolicy(
  requestType: string,
  body: { sla_minutes: number; enabled: boolean },
): PolicyOut {
  const known = policyStore.find((p) => p.request_type === requestType);
  if (!known) {
    throw problemErrorFor(422, "That request type is not one approval routes", [
      {
        field: "request_type",
        code: "unknown_request_type",
        message: `'${requestType}' is not a request type approval can configure`,
      },
    ]);
  }
  if (body.sla_minutes < 15) {
    throw problemErrorFor(422, "Request payload failed validation", [
      {
        field: "sla_minutes",
        code: "greater_than_equal",
        message: "Input should be greater than or equal to 15",
      },
    ]);
  }
  if (body.sla_minutes > 525600) {
    throw problemErrorFor(422, "Request payload failed validation", [
      {
        field: "sla_minutes",
        code: "less_than_equal",
        message: "Input should be less than or equal to 525600",
      },
    ]);
  }
  known.sla_minutes = body.sla_minutes;
  known.enabled = body.enabled;
  return { ...known };
}

/** Test-only: restores the fixture policy store to its default between test cases. */
export function __resetFixturePoliciesForTests(): void {
  policyStore = FIXTURE_POLICY_DEFAULTS.map((p) => ({ ...p }));
}

/* --------------------------- employee writes (fixture mode) --------------------------- */

/** The mutable, in-session copy of the seeded directory — mirrors `delegationStore`'s own
 *  "representative but internally consistent" contract so a fixture-mode create/edit/transition
 *  persists for the rest of the session. */
let employeeStore: EmployeeOut[] = EMPLOYEES.map((e) => ({ ...e }));

export function __listFixtureEmployees(): EmployeeOut[] {
  return employeeStore.map((e) => ({ ...e }));
}

export function __getFixtureEmployee(id: string): EmployeeOut | undefined {
  return employeeStore.find((e) => e.id === id);
}

let employeeSeq = 1000;

export function __createFixtureEmployee(body: EmployeeIn): EmployeeOut {
  if (employeeStore.some((e) => e.employee_no === body.employee_no)) {
    throw problemErrorFor(409, `An employee with the number '${body.employee_no}' already exists`, [
      { field: "employee_no", code: "duplicate", message: "This employee number is already in use." },
    ]);
  }
  employeeSeq += 1;
  const created: EmployeeOut = {
    id: uuid(9000 + employeeSeq),
    membership_id: null,
    employee_no: body.employee_no,
    full_name: body.full_name,
    designation: body.designation ?? null,
    job_title: body.job_title ?? null,
    department_id: body.department_id ?? null,
    location_code: body.location_code ?? null,
    employment_type: body.employment_type ?? "full_time",
    work_mode: body.work_mode ?? "office",
    date_of_joining: body.date_of_joining ?? null,
    reporting_manager_id: body.reporting_manager_id ?? null,
    dotted_line_manager_id: body.dotted_line_manager_id ?? null,
    status: "probation",
    date_of_exit: null,
    created_at: new Date().toISOString(),
    hierarchy_version: 1,
    pii: body.pii
      ? {
          date_of_birth: body.pii.date_of_birth ? "••••-••-••" : null,
          personal_email: body.pii.personal_email ? "••••@••••" : null,
          personal_phone: body.pii.personal_phone ? "•••• ••••" : null,
          pan: body.pii.pan ? "••••••••••" : null,
          aadhaar: body.pii.aadhaar ? "•••• •••• ••••" : null,
          uan: body.pii.uan ? "•••••••••••" : null,
          esi: body.pii.esi ? "•••••••••••••••••" : null,
          bank_account: body.pii.bank_account ? "•••••••••••••" : null,
          bank_ifsc: body.pii.bank_ifsc ?? null,
        }
      : null,
  };
  employeeStore = [...employeeStore, created];
  return created;
}

export function __updateFixtureEmployee(id: string, patch: EmployeePatch): EmployeeOut {
  const existing = employeeStore.find((e) => e.id === id);
  if (!existing) throw problemErrorFor(404, "That employee isn't available any more");
  const updated: EmployeeOut = {
    ...existing,
    ...("full_name" in patch ? { full_name: patch.full_name as string } : {}),
    ...("designation" in patch ? { designation: patch.designation ?? null } : {}),
    ...("job_title" in patch ? { job_title: patch.job_title ?? null } : {}),
    ...("department_id" in patch ? { department_id: patch.department_id ?? null } : {}),
    ...("location_code" in patch ? { location_code: patch.location_code ?? null } : {}),
    ...("employment_type" in patch ? { employment_type: patch.employment_type as EmploymentType } : {}),
    ...("work_mode" in patch ? { work_mode: patch.work_mode as WorkMode } : {}),
    ...("date_of_joining" in patch ? { date_of_joining: patch.date_of_joining ?? null } : {}),
    ...("date_of_exit" in patch ? { date_of_exit: patch.date_of_exit ?? null } : {}),
    ...("reporting_manager_id" in patch ? { reporting_manager_id: patch.reporting_manager_id ?? null } : {}),
    ...("dotted_line_manager_id" in patch
      ? { dotted_line_manager_id: patch.dotted_line_manager_id ?? null }
      : {}),
    hierarchy_version: existing.hierarchy_version + 1,
  };
  employeeStore = employeeStore.map((e) => (e.id === id ? updated : e));
  return updated;
}

const TRANSITIONS: Record<EmployeeOut["status"], EmployeeOut["status"][]> = {
  probation: ["active", "exited"],
  active: ["on_notice", "exited"],
  on_notice: ["active", "exited"],
  exited: [],
};

export function __transitionFixtureEmployee(id: string, body: EmployeeTransitionIn): EmployeeOut {
  const existing = employeeStore.find((e) => e.id === id);
  if (!existing) throw problemErrorFor(404, "That employee isn't available any more");
  if (!TRANSITIONS[existing.status].includes(body.to)) {
    throw problemErrorFor(422, `An employee cannot move from '${existing.status}' to '${body.to}'`);
  }
  const updated: EmployeeOut = {
    ...existing,
    status: body.to,
    date_of_exit: body.to === "exited" ? (existing.date_of_exit ?? new Date().toISOString().slice(0, 10)) : existing.date_of_exit,
  };
  employeeStore = employeeStore.map((e) => (e.id === id ? updated : e));
  return updated;
}

/** Fixture-mode unmask: illustrative only, and clearly so — never the shape a live unmask returns
 *  (that needs `amr: otp`, which fixture mode has no session to carry). */
export function __unmaskFixturePii(id: string, body: PiiUnmaskIn): EmployeePiiUnmasked {
  const existing = employeeStore.find((e) => e.id === id);
  if (!existing?.pii) throw problemErrorFor(404, "No sensitive details are on file for this employee");
  const out: EmployeePiiUnmasked = {};
  for (const field of body.fields) {
    if (field === "date_of_birth") out.date_of_birth = "1990-01-01";
    if (field === "personal_email") out.personal_email = "demo.personal@example.com";
    if (field === "personal_phone") out.personal_phone = "+91 90000 00000";
    if (field === "pan") out.pan = "ABCDE1234F"; // pragma: allowlist secret — a demo PAN, not a credential
    if (field === "aadhaar") out.aadhaar = "1234 5678 9012";
    if (field === "uan") out.uan = "100123456789";
    if (field === "esi") out.esi = "10012345670000123";
    if (field === "bank_account") out.bank_account = "000123456789012";
  }
  return out;
}

export function __listFixtureReports(managerId: string): EmployeeOut[] {
  return employeeStore.filter((e) => e.reporting_manager_id === managerId);
}

/** Test-only: restores the fixture employee directory between test cases. */
export function __resetFixtureEmployeesForTests(): void {
  employeeStore = EMPLOYEES.map((e) => ({ ...e }));
}

/* --------------------------- shifts & geofences (fixture mode) --------------------------- */

export const FIXTURE_SHIFTS: ShiftOut[] = [
  {
    id: uuid(8001),
    code: "GEN",
    name: "General shift",
    start_time: "09:30:00",
    end_time: "18:30:00",
    grace_minutes: 10,
    is_night: false,
    created_at: "2024-01-01T00:00:00Z",
  },
  {
    id: uuid(8002),
    code: "NIGHT",
    name: "Night shift",
    start_time: "21:00:00",
    end_time: "06:00:00",
    grace_minutes: 15,
    is_night: true,
    created_at: "2024-01-01T00:00:00Z",
  },
];

let shiftStore: ShiftOut[] = FIXTURE_SHIFTS.map((s) => ({ ...s }));
let shiftSeq = 9000;

export function __listFixtureShifts(): ShiftOut[] {
  return shiftStore.map((s) => ({ ...s }));
}

export function __createFixtureShift(body: ShiftIn): ShiftOut {
  if (shiftStore.some((s) => s.code === body.code)) {
    throw problemErrorFor(409, `A shift with the code '${body.code}' already exists`, [
      { field: "code", code: "duplicate", message: "This code is already in use." },
    ]);
  }
  shiftSeq += 1;
  const created: ShiftOut = {
    id: uuid(shiftSeq),
    code: body.code,
    name: body.name,
    start_time: body.start_time,
    end_time: body.end_time,
    grace_minutes: body.grace_minutes ?? 0,
    is_night: body.is_night ?? false,
    created_at: new Date().toISOString(),
  };
  shiftStore = [...shiftStore, created];
  return created;
}

export function __updateFixtureShift(id: string, patch: ShiftPatch): ShiftOut {
  const existing = shiftStore.find((s) => s.id === id);
  if (!existing) throw problemErrorFor(404, "That shift isn't part of this organization");
  const updated: ShiftOut = { ...existing, ...patch };
  shiftStore = shiftStore.map((s) => (s.id === id ? updated : s));
  return updated;
}

export function __deleteFixtureShift(id: string): void {
  const existing = shiftStore.find((s) => s.id === id);
  if (!existing) throw problemErrorFor(404, "That shift isn't part of this organization");
  shiftStore = shiftStore.filter((s) => s.id !== id);
}

export function __resetFixtureShiftsForTests(): void {
  shiftStore = FIXTURE_SHIFTS.map((s) => ({ ...s }));
}

export const FIXTURE_GEOFENCES: GeofenceOut[] = [
  {
    id: uuid(8501),
    location_code: "Bengaluru",
    name: "Bengaluru campus",
    center_lat: "12.9716",
    center_lng: "77.5946",
    radius_m: 300,
    created_at: "2024-01-01T00:00:00Z",
  },
];

let geofenceStore: GeofenceOut[] = FIXTURE_GEOFENCES.map((g) => ({ ...g }));
let geofenceSeq = 9500;

export function __listFixtureGeofences(): GeofenceOut[] {
  return geofenceStore.map((g) => ({ ...g }));
}

export function __createFixtureGeofence(body: GeofenceIn): GeofenceOut {
  if (geofenceStore.some((g) => g.location_code === body.location_code)) {
    throw problemErrorFor(409, `A geo-fence for '${body.location_code}' already exists`, [
      { field: "location_code", code: "duplicate", message: "This location already has a geo-fence." },
    ]);
  }
  geofenceSeq += 1;
  const created: GeofenceOut = { id: uuid(geofenceSeq), created_at: new Date().toISOString(), ...body };
  geofenceStore = [...geofenceStore, created];
  return created;
}

export function __updateFixtureGeofence(id: string, patch: GeofencePatch): GeofenceOut {
  const existing = geofenceStore.find((g) => g.id === id);
  if (!existing) throw problemErrorFor(404, "That geo-fence isn't part of this organization");
  const updated: GeofenceOut = { ...existing, ...patch };
  geofenceStore = geofenceStore.map((g) => (g.id === id ? updated : g));
  return updated;
}

export function __deleteFixtureGeofence(id: string): void {
  const existing = geofenceStore.find((g) => g.id === id);
  if (!existing) throw problemErrorFor(404, "That geo-fence isn't part of this organization");
  geofenceStore = geofenceStore.filter((g) => g.id !== id);
}

export function __resetFixtureGeofencesForTests(): void {
  geofenceStore = FIXTURE_GEOFENCES.map((g) => ({ ...g }));
}
