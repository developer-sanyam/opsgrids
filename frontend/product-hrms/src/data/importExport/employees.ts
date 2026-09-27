import { RowProblem, collectPages, defineImportEntity, makeLookup } from "@opsgrids/client";
import type { ImportColumn, Lookup, LookupSet, SheetValue, UpdateSpec } from "@opsgrids/client";

import { createEmployee, listEmployees, updateEmployee } from "@/api/client";
import type {
  EmployeeIn,
  EmployeeOut,
  EmployeePatch,
  EmployeeStatus,
  EmploymentType,
  WorkMode,
} from "@/api/types";
import { employeeStatusLabel } from "@/lib/format";

import { departmentEntries, loadOrgUnitTree } from "./departments";
import { loadLocations, locationEntries } from "./locations";
import {
  columnOf,
  labelOfOption,
  optionOf,
  requiredTextOf,
  resolveLookup,
  resolveReference,
  sameKey,
  storedText,
  textOf,
} from "./rowValues";

/** What the Employees screen tracks, and so what an export of it must respect. */
export interface EmployeeFilters {
  q: string;
  status: EmployeeStatus | "all";
  departmentId: string | undefined;
}

const EMPLOYMENT_OPTIONS = [
  { value: "full_time", label: "Full-time" },
  { value: "part_time", label: "Part-time" },
  { value: "contract", label: "Contract" },
  { value: "intern", label: "Intern" },
] as const satisfies readonly { value: EmploymentType; label: string }[];

const WORK_MODE_OPTIONS = [
  { value: "office", label: "Office" },
  { value: "field", label: "Field" },
  { value: "remote", label: "Remote" },
  { value: "hybrid", label: "Hybrid" },
] as const satisfies readonly { value: WorkMode; label: string }[];

const EMPLOYEE_STATUSES: readonly EmployeeStatus[] = ["probation", "active", "on_notice", "exited"];
const STATUS_OPTIONS = EMPLOYEE_STATUSES.map((value) => ({
  value,
  label: employeeStatusLabel(value),
}));

const DEPARTMENTS = "departments";
const LOCATIONS = "locations";
const MANAGERS = "managers";

/** Said under a lookup column's "is not in the list" error, so the fix is in the row itself. */
const DEPARTMENT_HINT = "Use a department name or code from the Lists sheet.";
const LOCATION_HINT =
  "Use a location code or name from the Lists sheet. If that sheet lists no locations, your account cannot read them: leave this cell blank.";
const MANAGER_HINT =
  "The manager must already be an employee. Import managers first, then run this file again: rows already imported are skipped.";

/** The same, for a row that updates an employee, where a blank cell keeps what is there and rows already added show as No change. */
const DEPARTMENT_HINT_UPDATE =
  "Use a department name or code from the Lists sheet. To remove the department, write [clear]; a blank cell keeps it.";
const LOCATION_HINT_UPDATE =
  "Use a location code or name from the Lists sheet. If that sheet lists no locations, your account cannot read them: leave this cell blank to keep the location as it is.";
const MANAGER_HINT_UPDATE =
  "The manager must already be an employee. Add the manager first, then run this file again: rows already added show as No change. To remove a manager, write [clear].";

const columns: readonly ImportColumn[] = [
  {
    key: "employee_no",
    header: "Employee number",
    kind: "text",
    required: true,
    maxLength: 40,
    pattern: /^[A-Za-z0-9_-]+$/,
    patternHint: "Letters, digits, hyphens and underscores, with no spaces.",
    help: "The employee's number. It must be unique in your organisation and cannot be changed later.",
  },
  {
    key: "full_name",
    header: "Full name",
    kind: "text",
    required: true,
    maxLength: 200,
    help: "The employee's full name.",
  },
  {
    key: "designation",
    header: "Designation",
    kind: "text",
    maxLength: 120,
    help: "Their designation, for example Senior Engineer.",
  },
  {
    key: "job_title",
    header: "Job title",
    kind: "text",
    maxLength: 120,
    help: "Their job title or functional group, for example Engineering.",
  },
  {
    key: "department",
    header: "Department",
    kind: "text",
    lookup: DEPARTMENTS,
    lookupHint: DEPARTMENT_HINT,
    lookupHintUpdate: DEPARTMENT_HINT_UPDATE,
    help: "The department's name as your organisation lists it, or its code. Where two departments share a name, write it as Parent / Child, or use the code.",
  },
  {
    key: "location_code",
    header: "Location code",
    kind: "text",
    lookup: LOCATIONS,
    lookupHint: LOCATION_HINT,
    lookupHintUpdate: LOCATION_HINT_UPDATE,
    help: "The office or site they work from: its code, for example BLR-HQ, or its name. It must be one of your organisation's locations, or location-based roles will not see this employee.",
  },
  {
    key: "employment_type",
    header: "Employment type",
    kind: "enum",
    options: EMPLOYMENT_OPTIONS,
    help: "How they are employed. On a new employee, leave blank for Full-time.",
  },
  {
    key: "work_mode",
    header: "Work mode",
    kind: "enum",
    options: WORK_MODE_OPTIONS,
    help: "Where they usually work. On a new employee, leave blank for Office.",
  },
  {
    key: "date_of_joining",
    header: "Date of joining",
    kind: "date",
    help: "The day they joined, for example 2026-04-01.",
  },
  {
    key: "reporting_manager",
    header: "Manager employee number",
    kind: "text",
    lookup: MANAGERS,
    lookupHint: MANAGER_HINT,
    lookupHintUpdate: MANAGER_HINT_UPDATE,
    help: "The employee number of their reporting manager, who must already be an employee.",
  },
  {
    key: "dotted_line_manager",
    header: "Dotted-line manager employee number",
    kind: "text",
    lookup: MANAGERS,
    lookupHint: MANAGER_HINT,
    lookupHintUpdate: MANAGER_HINT_UPDATE,
    help: "The employee number of an informational second manager, who must already be an employee.",
  },
  {
    key: "status",
    header: "Status",
    kind: "enum",
    options: STATUS_OPTIONS,
    readOnly: true,
    help: "Where they are in their employment. Set by the system: everyone imported starts on Probation.",
  },
  {
    key: "date_of_exit",
    header: "Date of exit",
    kind: "date",
    readOnly: true,
    help: "The day they left. Set by the system when an employee is marked as exited.",
  },
  {
    key: "created_at",
    header: "Created",
    kind: "text",
    readOnly: true,
    help: "When the record was created. Set by the system.",
  },
  {
    key: "id",
    header: "Record ID",
    kind: "text",
    readOnly: true,
    help: "The system's id for this employee. Set by the system.",
  },
];

const sampleRows: readonly Record<string, SheetValue>[] = [
  {
    employee_no: "EMP-1001",
    full_name: "Aarav Mehta",
    designation: "Senior Engineer",
    job_title: "Engineering",
    department: "Engineering",
    location_code: "BLR-HQ",
    employment_type: "Full-time",
    work_mode: "Hybrid",
    date_of_joining: "2025-06-02",
  },
  {
    employee_no: "EMP-1002",
    full_name: "Priya Nair",
    designation: "HR Executive",
    job_title: "People Operations",
    department: "Human Resources",
    location_code: "BLR-HQ",
    employment_type: "Full-time",
    work_mode: "Office",
    date_of_joining: "2026-01-12",
    reporting_manager: "EMP-0007",
  },
  {
    employee_no: "EMP-1003",
    full_name: "Karan Malhotra",
    designation: "Field Sales Executive",
    job_title: "Sales",
    department: "Sales",
    location_code: "MUM-01",
    employment_type: "Contract",
    work_mode: "Field",
    date_of_joining: "2026-03-02",
    reporting_manager: "EMP-0007",
    dotted_line_manager: "EMP-0012",
  },
];

const notes = [
  "Employee numbers must be unique. With \"Add new records only\", a row whose number already exists is skipped, never updated.",
  "A manager is found by employee number and must already be an employee. A manager created in this same file is not found and the row is refused: import the managers first, then run this file again.",
  "A department is found by its name or by its code. Where two departments share a name, write it as Parent / Child, or use the code; the Lists sheet shows both.",
  "A location is found by its code or its name, and is saved as its code. An employee whose location is not one of your organisation's locations is not seen by location-based roles, so a location that is not on the Lists sheet is refused.",
  "Everyone imported starts on Probation. Status cannot be set from a sheet.",
  "A row with a Record ID came out of the system and is already there, so \"Add new records only\" refuses it. To use an exported file as a starting point, delete its Record ID column first. With \"Add new and update existing\", the Record ID says which employee to update.",
  "Personal and sensitive details (date of birth, personal email and phone, PAN, Aadhaar, UAN, ESI, bank account) cannot be imported. They are added one employee at a time and stay masked.",
  "Updating: status and date of exit cannot be changed from a sheet, and neither can the personal and sensitive details above. Employees who have exited can still be updated.",
  "Updating: a manager is sent only when it differs from the employee's current one. An employee cannot be their own manager, and a reporting line that would become circular is refused by the server. The server lets only someone with organisation-wide access update employees; for anyone else every updated row is refused.",
];

/** The sheet text for a department or manager id: blank when there is none or the caller cannot name it. */
function referenceLabel(lookup: Lookup | undefined, id: string | null): string {
  return id === null ? "" : (lookup?.labelOf(id) ?? "");
}

async function loadLookups(signal: AbortSignal): Promise<LookupSet> {
  const [tree, locations, employees] = await Promise.all([
    loadOrgUnitTree(),
    loadLocations(),
    collectPages((page, size) => listEmployees({ page, size }), {
      signal,
      key: (employee) => employee.id,
    }),
  ]);
  return {
    [DEPARTMENTS]: makeLookup(departmentEntries(tree)),
    [LOCATIONS]: makeLookup(locationEntries(locations)),
    [MANAGERS]: makeLookup(
      employees.map((employee) => ({ label: employee.employee_no, value: employee.id })),
    ),
  };
}

/**
 * How an existing employee is updated from a sheet. Every field here is a key of `PATCH
 * /employees/{id}`, whose schema silently ignores keys it does not know (employee number, status)
 * and answers 200 with nothing changed, so an identity column is `immutable` and refused rather
 * than sent. The endpoint has no null guard either: only the fields the survey found nullable
 * carry a `clear`, and it is `null`, never `""` (an empty location code would break
 * location-scoped roles). Department and location code are not validated by the server, so
 * `resolve` through the lookups is the only guard against an id or code that does not exist.
 */
const employeeUpdate: UpdateSpec<EmployeeOut> = {
  loadExisting: (ctx) =>
    collectPages((page, size) => listEmployees({ page, size }), {
      signal: ctx.signal,
      key: (employee) => employee.id,
    }),
  idOf: (employee) => employee.id,
  keyColumn: "employee_no",
  keyOf: (employee) => employee.employee_no,
  fields: [
    { column: "employee_no", current: (e) => e.employee_no, immutable: true, same: sameKey },
    { column: "full_name", current: (e) => e.full_name },
    { column: "designation", current: (e) => storedText(e.designation), clear: null },
    { column: "job_title", current: (e) => storedText(e.job_title), clear: null },
    {
      column: "department",
      field: "department_id" satisfies keyof EmployeePatch,
      current: (e) => e.department_id,
      resolve: resolveLookup(columnOf(columns, "department")),
      clear: null,
    },
    {
      column: "location_code",
      current: (e) => storedText(e.location_code),
      resolve: resolveLookup(columnOf(columns, "location_code")),
      clear: null,
    },
    { column: "employment_type", current: (e) => e.employment_type },
    { column: "work_mode", current: (e) => e.work_mode },
    { column: "date_of_joining", current: (e) => e.date_of_joining, clear: null },
    {
      column: "reporting_manager",
      field: "reporting_manager_id" satisfies keyof EmployeePatch,
      current: (e) => e.reporting_manager_id,
      resolve: resolveLookup(columnOf(columns, "reporting_manager")),
      clear: null,
    },
    {
      column: "dotted_line_manager",
      field: "dotted_line_manager_id" satisfies keyof EmployeePatch,
      current: (e) => e.dotted_line_manager_id,
      resolve: resolveLookup(columnOf(columns, "dotted_line_manager")),
      clear: null,
    },
  ],
  check(row, employee, _changed, { lookups }) {
    const namesThemselves = (key: string): boolean => {
      const label = textOf(row, key);
      return label !== null && lookups[MANAGERS]?.resolve(label) === employee.id;
    };
    if (namesThemselves("reporting_manager")) throw new RowProblem("An employee cannot report to themselves.");
    if (namesThemselves("dotted_line_manager")) {
      throw new RowProblem("An employee cannot be their own dotted-line manager.");
    }
  },
  async send(employee, body) {
    // The body holds only the keys this spec's fields name, all of them keys of `EmployeePatch`.
    await updateEmployee(employee.id, body as EmployeePatch);
  },
};

export const employeesEntity = defineImportEntity<EmployeeIn, EmployeeOut, EmployeeFilters>({
  id: "hrms.employees",
  noun: "employees",
  nounSingular: "employee",
  fileStem: "employees",
  columns,
  sampleRows,
  notes,
  recordIdKey: "id",
  loadLookups,
  update: employeeUpdate,
  toPayload(row, { lookups }) {
    const body: EmployeeIn = {
      employee_no: requiredTextOf(row, "employee_no", "Employee number"),
      full_name: requiredTextOf(row, "full_name", "Full name"),
    };
    const designation = textOf(row, "designation");
    if (designation !== null) body.designation = designation;
    const jobTitle = textOf(row, "job_title");
    if (jobTitle !== null) body.job_title = jobTitle;
    const department = textOf(row, "department");
    if (department !== null) body.department_id = resolveReference(lookups[DEPARTMENTS], department);
    const location = textOf(row, "location_code");
    if (location !== null) body.location_code = resolveReference(lookups[LOCATIONS], location);
    const employmentType = optionOf(row, "employment_type", EMPLOYMENT_OPTIONS);
    if (employmentType !== null) body.employment_type = employmentType;
    const workMode = optionOf(row, "work_mode", WORK_MODE_OPTIONS);
    if (workMode !== null) body.work_mode = workMode;
    const joined = textOf(row, "date_of_joining");
    if (joined !== null) body.date_of_joining = joined;
    const manager = textOf(row, "reporting_manager");
    if (manager !== null) body.reporting_manager_id = resolveReference(lookups[MANAGERS], manager);
    const dotted = textOf(row, "dotted_line_manager");
    if (dotted !== null) body.dotted_line_manager_id = resolveReference(lookups[MANAGERS], dotted);
    return body;
  },
  async create(payload) {
    const employee = await createEmployee(payload);
    return { id: employee.id };
  },
  export: {
    fetchPage: ({ page, size, filters }) =>
      listEmployees({
        q: filters.q,
        status: filters.status,
        departmentId: filters.departmentId,
        page,
        size,
      }),
    toRow: (employee, lookups) => ({
      employee_no: employee.employee_no,
      full_name: employee.full_name,
      designation: employee.designation,
      job_title: employee.job_title,
      department: referenceLabel(lookups[DEPARTMENTS], employee.department_id),
      // The code as stored, whether or not the caller may read the list of locations.
      location_code: employee.location_code,
      employment_type: labelOfOption(EMPLOYMENT_OPTIONS, employee.employment_type),
      work_mode: labelOfOption(WORK_MODE_OPTIONS, employee.work_mode),
      date_of_joining: employee.date_of_joining,
      reporting_manager: referenceLabel(lookups[MANAGERS], employee.reporting_manager_id),
      dotted_line_manager: referenceLabel(lookups[MANAGERS], employee.dotted_line_manager_id),
      status: employeeStatusLabel(employee.status),
      date_of_exit: employee.date_of_exit,
      created_at: employee.created_at,
      id: employee.id,
    }),
  },
});
