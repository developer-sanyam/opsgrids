/**
 * Realistic fixture data, shaped exactly like the real API responses so the whole UI renders
 * without a live login. Swapped out by the `USE_FIXTURES` flag in `client.ts`.
 */

import type {
  AttendanceRecord,
  AttendanceStatus,
  EmployeeOut,
  EmploymentType,
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

// Deterministic per-employee attendance so the table and the stats always agree.
function attendanceFor(emp: EmployeeOut, index: number): AttendanceRecord {
  // A stable spread: most present, a few late, a couple absent (on leave / no punch).
  const bucket = index % 10;
  let status: AttendanceStatus;
  if (bucket === 3 || bucket === 8) status = "absent";
  else if (bucket === 1 || bucket === 6) status = "late";
  else status = "present";

  const source: PunchSource =
    emp.work_mode === "field" ? "field" : emp.work_mode === "remote" ? "mobile" : "web";

  if (status === "absent") {
    return {
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
    };
  }

  const inHour = status === "late" ? 10 : 9;
  const inMin = status === "late" ? 24 + ((index * 7) % 30) : (index * 5) % 55;
  const checkIn = `${TODAY}T${pad(inHour)}:${pad(inMin)}:00Z`;
  const outHour = 18 + (index % 2);
  const outMin = (index * 11) % 55;
  const checkOut = `${TODAY}T${pad(outHour)}:${pad(outMin)}:00Z`;
  const worked = (outHour * 60 + outMin) - (inHour * 60 + inMin);

  return {
    id: `att-${emp.id}`,
    employee_id: emp.id,
    employee_no: emp.employee_no,
    full_name: emp.full_name,
    department_id: emp.department_id,
    job_title: emp.job_title,
    work_date: TODAY,
    check_in: checkIn,
    check_out: checkOut,
    worked_minutes: worked,
    source,
    status,
  };
}

function pad(n: number): string {
  return n.toString().padStart(2, "0");
}

export const ATTENDANCE_TODAY: AttendanceRecord[] = EMPLOYEES.map((e, i) =>
  attendanceFor(e, i),
);
