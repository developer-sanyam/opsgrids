/**
 * Realistic fixture data, shaped exactly like the real API responses so the whole UI renders
 * without a live login. Swapped out by the `USE_FIXTURES` flag in `client.ts`.
 */

import type {
  ApprovalChainOut,
  AttendanceRecord,
  AttendanceStatus,
  EmployeeOut,
  EmploymentType,
  InboxItemOut,
  LocationOut,
  OrganizationOut,
  RequestDetailOut,
  RequestListItemOut,
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
  status: s.status,
  date_of_exit: null,
  created_at: `${s.joined}T09:00:00Z`,
  pii: null,
}));

/* --------------------------- attendance for today --------------------------- */

const TODAY = "2026-08-24";

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

  const source: PunchSource =
    emp.work_mode === "field" ? "field" : emp.work_mode === "remote" ? "mobile" : "web";

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
    due_at: null,
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
    steps: stepsFor({ requester: EMILY, approver: SNEHA, openedAt: `${TODAY}T12:50:00Z` }),
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
