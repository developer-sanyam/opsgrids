/**
 * Create and edit — one form, mounted only while the dialog is open (`FormDialog` unmounts its
 * children on close, which is the reset). Fields mirror `EmployeeIn`/`EmployeePatch` exactly
 * (`services/employee/app/schemas.py`); `employee_no` is immutable once created.
 *
 * PII is masked by default: on edit every sensitive field starts **blank**, with the mask shown
 * only as a placeholder, and only a field the user actually typed into is sent (§ CLAUDE.md "PII
 * fields are masked by default"). `pii` is omitted from the request entirely when nothing in the
 * group was touched.
 */
import { useEffect, useRef, useState } from "react";

import { ApiError, problemMessage, useMutation } from "@opsgrids/client";
import type { ComboboxOption, StepItem } from "@opsgrids/ui";
import { FormDialog, FormSection, Select, Stepper, TextField } from "@opsgrids/ui";

import { createEmployee, getEmployee, updateEmployee } from "@/api/client";
import { getOrgUnitNameById } from "@/api/orgUnits";
import { useZoneLookup } from "@/lib/zone";
import type {
  EmployeeIn,
  EmployeeOut,
  EmployeePatch,
  EmployeePiiIn,
  EmploymentType,
  WorkMode,
} from "@/api/types";
import { EmployeePicker } from "./EmployeePicker";

const EMPLOYMENT_OPTIONS = [
  { value: "full_time", label: "Full-time" },
  { value: "part_time", label: "Part-time" },
  { value: "contract", label: "Contract" },
  { value: "intern", label: "Intern" },
];

const WORK_MODE_OPTIONS = [
  { value: "office", label: "Office" },
  { value: "field", label: "Field" },
  { value: "remote", label: "Remote" },
  { value: "hybrid", label: "Hybrid" },
];

const STEPS: StepItem[] = [
  { id: "basic", title: "Basic details", description: "Identity, role and dates" },
  { id: "reporting", title: "Reporting & sensitive details", description: "Manager and optional PII" },
];

/** The wire names of everything the basic-details step owns — used to send a validation problem
 *  there back to the step that can fix it. */
const BASIC_FIELDS = new Set([
  "employee_no",
  "full_name",
  "designation",
  "job_title",
  "department_id",
  "location_code",
  "employment_type",
  "work_mode",
  "date_of_joining",
  "date_of_exit",
]);

interface PiiDraft {
  date_of_birth: string;
  personal_email: string;
  personal_phone: string;
  pan: string;
  aadhaar: string;
  uan: string;
  esi: string;
  bank_account: string;
  bank_ifsc: string;
}

const BLANK_PII: PiiDraft = {
  date_of_birth: "",
  personal_email: "",
  personal_phone: "",
  pan: "",
  aadhaar: "",
  uan: "",
  esi: "",
  bank_account: "",
  bank_ifsc: "",
};

function toPiiIn(draft: PiiDraft): EmployeePiiIn | undefined {
  const entries = Object.entries(draft).filter(([, v]) => v.trim() !== "");
  if (entries.length === 0) return undefined;
  return Object.fromEntries(entries.map(([k, v]) => [k, v.trim()])) as EmployeePiiIn;
}

async function resolveManagerOption(id: string | null): Promise<ComboboxOption | null> {
  if (!id) return null;
  try {
    const emp = await getEmployee(id);
    return { id: emp.id, label: emp.full_name, sublabel: emp.employee_no };
  } catch {
    return { id, label: "Employee record unavailable" };
  }
}

interface EmployeeFormProps {
  employee: EmployeeOut | null;
  onClose: () => void;
  onSaved: (employee: EmployeeOut) => void;
}

export function EmployeeForm({ employee, onClose, onSaved }: EmployeeFormProps) {
  const editing = employee !== null;
  const zoneLookup = useZoneLookup();
  const [departments, setDepartments] = useState<ReadonlyMap<string, string>>(new Map());
  const [step, setStep] = useState(0);

  const [employeeNo, setEmployeeNo] = useState(employee?.employee_no ?? "");
  const [fullName, setFullName] = useState(employee?.full_name ?? "");
  const [designation, setDesignation] = useState(employee?.designation ?? "");
  const [jobTitle, setJobTitle] = useState(employee?.job_title ?? "");
  const [departmentId, setDepartmentId] = useState(employee?.department_id ?? "");
  const [locationCode, setLocationCode] = useState(employee?.location_code ?? "");
  const [employmentType, setEmploymentType] = useState<EmploymentType>(employee?.employment_type ?? "full_time");
  const [workMode, setWorkMode] = useState<WorkMode>(employee?.work_mode ?? "office");
  const [dateOfJoining, setDateOfJoining] = useState(employee?.date_of_joining ?? "");
  // Gap 12: `date_of_exit` is only ever settable once an employee is already `exited` — the
  // transition itself auto-stamps it, and this is the later correction the service now allows.
  const [dateOfExit, setDateOfExit] = useState(employee?.date_of_exit ?? "");
  const [manager, setManager] = useState<ComboboxOption | null>(null);
  const [dottedManager, setDottedManager] = useState<ComboboxOption | null>(null);
  const [sensitiveOpen, setSensitiveOpen] = useState(false);
  const [pii, setPii] = useState<PiiDraft>(BLANK_PII);

  const [employeeNoError, setEmployeeNoError] = useState<string | undefined>(undefined);
  const [fullNameError, setFullNameError] = useState<string | undefined>(undefined);

  const employeeNoRef = useRef<HTMLInputElement>(null);
  const fullNameRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    let active = true;
    getOrgUnitNameById().then((map) => {
      if (active) setDepartments(map);
    });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    let active = true;
    resolveManagerOption(employee?.reporting_manager_id ?? null).then((opt) => {
      if (active) setManager(opt);
    });
    resolveManagerOption(employee?.dotted_line_manager_id ?? null).then((opt) => {
      if (active) setDottedManager(opt);
    });
    return () => {
      active = false;
    };
    // Runs once per open — `employee` identity is stable for the dialog's lifetime (it unmounts
    // on close, which is the reset).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const save = useMutation(
    (input: EmployeeIn | EmployeePatch) =>
      employee ? updateEmployee(employee.id, input as EmployeePatch) : createEmployee(input as EmployeeIn),
    { onSuccess: (saved) => { onClose(); onSaved(saved); } },
  );

  // A field error the service names on the basic-details step (say, a duplicate `employee_no`)
  // belongs to a control the reporting step doesn't render — jump back to where it can be seen.
  useEffect(() => {
    if (Object.keys(save.fieldErrors).some((field) => BASIC_FIELDS.has(field))) setStep(0);
  }, [save.fieldErrors]);

  /** Employee number and full name — everything the basic-details step must have before moving
   *  on, or before the record is actually saved. */
  function validateBasics(): boolean {
    setEmployeeNoError(undefined);
    setFullNameError(undefined);

    if (!editing && !/^[A-Za-z0-9_-]{1,40}$/.test(employeeNo.trim())) {
      setEmployeeNoError("1–40 characters: letters, numbers, hyphens and underscores only.");
      employeeNoRef.current?.focus();
      return false;
    }
    if (fullName.trim().length < 1) {
      setFullNameError("Full name is required.");
      fullNameRef.current?.focus();
      return false;
    }
    return true;
  }

  function continueToReporting() {
    if (validateBasics()) setStep(1);
  }

  function submit() {
    if (!validateBasics()) {
      setStep(0);
      return;
    }

    const piiIn = sensitiveOpen ? toPiiIn(pii) : undefined;

    if (!editing) {
      const body: EmployeeIn = {
        employee_no: employeeNo.trim(),
        full_name: fullName.trim(),
        designation: designation.trim() || null,
        job_title: jobTitle.trim() || null,
        department_id: departmentId || null,
        location_code: locationCode || null,
        employment_type: employmentType,
        work_mode: workMode,
        date_of_joining: dateOfJoining || null,
        reporting_manager_id: manager?.id ?? null,
        dotted_line_manager_id: dottedManager?.id ?? null,
        ...(piiIn ? { pii: piiIn } : {}),
      };
      void save.mutate(body);
      return;
    }

    // Edit: send only what changed from the record we opened with, so an untouched field is left
    // alone and a cleared one is sent as an explicit `null` (`EmployeePatch`, `exclude_unset`).
    const body: EmployeePatch = {};
    if (fullName.trim() !== employee.full_name) body.full_name = fullName.trim();
    if ((designation.trim() || null) !== employee.designation) body.designation = designation.trim() || null;
    if ((jobTitle.trim() || null) !== employee.job_title) body.job_title = jobTitle.trim() || null;
    if ((departmentId || null) !== employee.department_id) body.department_id = departmentId || null;
    if ((locationCode || null) !== employee.location_code) body.location_code = locationCode || null;
    if (employmentType !== employee.employment_type) body.employment_type = employmentType;
    if (workMode !== employee.work_mode) body.work_mode = workMode;
    if ((dateOfJoining || null) !== employee.date_of_joining) body.date_of_joining = dateOfJoining || null;
    if (employee.status === "exited" && (dateOfExit || null) !== employee.date_of_exit) {
      body.date_of_exit = dateOfExit || null;
    }
    if ((manager?.id ?? null) !== employee.reporting_manager_id) body.reporting_manager_id = manager?.id ?? null;
    if ((dottedManager?.id ?? null) !== employee.dotted_line_manager_id)
      body.dotted_line_manager_id = dottedManager?.id ?? null;
    if (piiIn) body.pii = piiIn;
    void save.mutate(body);
  }

  const departmentOptions = [
    { value: "", label: "No department" },
    ...[...departments.entries()].map(([id, name]) => ({ value: id, label: name })),
  ];
  const locationOptions = [
    { value: "", label: "No location" },
    ...(zoneLookup
      ? Object.keys(zoneLookup.locations).map((code) => ({
          value: code,
          label: zoneLookup.names[code] ?? code,
        }))
      : []),
  ];

  const topLevelError =
    save.error && Object.keys(save.fieldErrors).length === 0
      ? {
          error: problemMessage(save.error),
          ...(save.error instanceof ApiError && save.error.problem?.request_id
            ? { reference: save.error.problem.request_id }
            : {}),
        }
      : {};

  const basicInvalid = Boolean(
    employeeNoError ??
      fullNameError ??
      Object.keys(save.fieldErrors).find((field) => BASIC_FIELDS.has(field)),
  );
  const reportingInvalid = Object.keys(save.fieldErrors).some((field) => !BASIC_FIELDS.has(field));
  const stepItems: StepItem[] = STEPS.map((item, index) => ({
    ...item,
    invalid: index === 0 ? basicInvalid : reportingInvalid,
  }));

  const dirty =
    employeeNo !== (employee?.employee_no ?? "") ||
    fullName !== (employee?.full_name ?? "") ||
    designation !== (employee?.designation ?? "") ||
    jobTitle !== (employee?.job_title ?? "") ||
    departmentId !== (employee?.department_id ?? "") ||
    locationCode !== (employee?.location_code ?? "") ||
    employmentType !== (employee?.employment_type ?? "full_time") ||
    workMode !== (employee?.work_mode ?? "office") ||
    dateOfJoining !== (employee?.date_of_joining ?? "") ||
    dateOfExit !== (employee?.date_of_exit ?? "") ||
    (manager?.id ?? null) !== (employee?.reporting_manager_id ?? null) ||
    (dottedManager?.id ?? null) !== (employee?.dotted_line_manager_id ?? null) ||
    (sensitiveOpen && toPiiIn(pii) !== undefined);

  function handleSubmit() {
    if (step === 0) continueToReporting();
    else submit();
  }

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={handleSubmit}
      title={editing ? `Edit ${employee.full_name}` : "New employee"}
      submitLabel={step === 0 ? "Continue" : editing ? "Save changes" : "Create employee"}
      pendingLabel={editing ? "Saving…" : "Creating…"}
      pending={save.pending}
      initialFocusRef={editing ? fullNameRef : employeeNoRef}
      size="lg"
      dirty={dirty}
      {...topLevelError}
    >
      <Stepper steps={stepItems} current={step} onStepChange={setStep} label="Employee form progress" />

      {step === 0 ? (
      <FormSection title="Basic details" headingLevel={3}>
        <TextField
          ref={employeeNoRef}
          label="Employee number"
          value={employeeNo}
          onChange={(e) => setEmployeeNo(e.target.value)}
          disabled={editing}
          helper={editing ? "Immutable once created." : "Letters, numbers, hyphens and underscores."}
          required={!editing}
          error={employeeNoError ?? save.fieldErrors.employee_no}
        />
        <TextField
          ref={fullNameRef}
          label="Full name"
          value={fullName}
          onChange={(e) => setFullName(e.target.value)}
          required
          error={fullNameError ?? save.fieldErrors.full_name}
        />
        <TextField
          label="Designation"
          value={designation}
          onChange={(e) => setDesignation(e.target.value)}
          optional
          error={save.fieldErrors.designation}
        />
        <TextField
          label="Job title"
          value={jobTitle}
          onChange={(e) => setJobTitle(e.target.value)}
          optional
          error={save.fieldErrors.job_title}
        />
        <Select
          label="Department"
          value={departmentId}
          onChange={(e) => setDepartmentId(e.target.value)}
          options={departmentOptions}
          error={save.fieldErrors.department_id}
        />
        <Select
          label="Location"
          value={locationCode}
          onChange={(e) => setLocationCode(e.target.value)}
          options={locationOptions}
          error={save.fieldErrors.location_code}
        />
        <Select
          label="Employment type"
          value={employmentType}
          onChange={(e) => setEmploymentType(e.target.value as typeof employmentType)}
          options={EMPLOYMENT_OPTIONS}
          error={save.fieldErrors.employment_type}
        />
        <Select
          label="Work mode"
          value={workMode}
          onChange={(e) => setWorkMode(e.target.value as typeof workMode)}
          options={WORK_MODE_OPTIONS}
          error={save.fieldErrors.work_mode}
        />
        <TextField
          label="Date of joining"
          type="date"
          value={dateOfJoining}
          onChange={(e) => setDateOfJoining(e.target.value)}
          optional
          error={save.fieldErrors.date_of_joining}
        />
        {editing && employee.status === "exited" ? (
          <TextField
            label="Date of exit"
            type="date"
            value={dateOfExit}
            onChange={(e) => setDateOfExit(e.target.value)}
            min={dateOfJoining || undefined}
            helper="A correction to the date the exit transition stamped."
            error={save.fieldErrors.date_of_exit}
          />
        ) : null}
      </FormSection>
      ) : (
      <FormSection title="Reporting & sensitive details" headingLevel={3}>
      <EmployeePicker
        label="Reporting manager"
        value={manager}
        onChange={setManager}
        error={save.fieldErrors.reporting_manager_id}
      />
      <EmployeePicker
        label="Dotted-line manager"
        value={dottedManager}
        onChange={setDottedManager}
        helper="Informational only — not part of the approval chain."
        error={save.fieldErrors.dotted_line_manager_id}
      />

      <div className="col-span-full rounded-tile border border-line">
        <button
          type="button"
          onClick={() => setSensitiveOpen((v) => !v)}
          aria-expanded={sensitiveOpen}
          className="flex w-full items-center justify-between px-4 py-3 text-sm font-semibold text-ink"
        >
          Sensitive details
          <span className="text-xs font-medium text-muted">
            {sensitiveOpen ? "Hide" : editing ? "Change" : "Add"}
          </span>
        </button>
        {sensitiveOpen ? (
          <div className="grid grid-cols-1 gap-4 border-t border-line p-4 sm:grid-cols-2">
            <p className="text-xs text-muted sm:col-span-2">
              {editing
                ? "Every field here is blank on purpose — the stored values are masked and never sent back to you. Type a new value only for what you want to change."
                : "Optional. Sealed on write; never stored or logged as plaintext."}
            </p>
            <TextField
              label="Date of birth"
              type="date"
              value={pii.date_of_birth}
              onChange={(e) => setPii((p) => ({ ...p, date_of_birth: e.target.value }))}
              optional
              error={save.fieldErrors["pii.date_of_birth"]}
            />
            <TextField
              label="Personal email"
              type="email"
              value={pii.personal_email}
              onChange={(e) => setPii((p) => ({ ...p, personal_email: e.target.value }))}
              placeholder={editing ? (employee.pii?.personal_email ?? undefined) : undefined}
              optional
              error={save.fieldErrors["pii.personal_email"]}
            />
            <TextField
              label="Personal phone"
              value={pii.personal_phone}
              onChange={(e) => setPii((p) => ({ ...p, personal_phone: e.target.value }))}
              placeholder={editing ? (employee.pii?.personal_phone ?? undefined) : undefined}
              optional
              error={save.fieldErrors["pii.personal_phone"]}
            />
            <TextField
              label="PAN"
              value={pii.pan}
              onChange={(e) => setPii((p) => ({ ...p, pan: e.target.value }))}
              placeholder={editing ? (employee.pii?.pan ?? undefined) : undefined}
              optional
              error={save.fieldErrors["pii.pan"]}
            />
            <TextField
              label="Aadhaar"
              value={pii.aadhaar}
              onChange={(e) => setPii((p) => ({ ...p, aadhaar: e.target.value }))}
              placeholder={editing ? (employee.pii?.aadhaar ?? undefined) : undefined}
              optional
              error={save.fieldErrors["pii.aadhaar"]}
            />
            <TextField
              label="UAN"
              value={pii.uan}
              onChange={(e) => setPii((p) => ({ ...p, uan: e.target.value }))}
              placeholder={editing ? (employee.pii?.uan ?? undefined) : undefined}
              optional
              error={save.fieldErrors["pii.uan"]}
            />
            <TextField
              label="ESI"
              value={pii.esi}
              onChange={(e) => setPii((p) => ({ ...p, esi: e.target.value }))}
              placeholder={editing ? (employee.pii?.esi ?? undefined) : undefined}
              optional
              error={save.fieldErrors["pii.esi"]}
            />
            <TextField
              label="Bank account"
              value={pii.bank_account}
              onChange={(e) => setPii((p) => ({ ...p, bank_account: e.target.value }))}
              placeholder={editing ? (employee.pii?.bank_account ?? undefined) : undefined}
              optional
              error={save.fieldErrors["pii.bank_account"]}
            />
            <TextField
              label="Bank IFSC"
              value={pii.bank_ifsc}
              onChange={(e) => setPii((p) => ({ ...p, bank_ifsc: e.target.value }))}
              placeholder={editing ? (employee.pii?.bank_ifsc ?? undefined) : undefined}
              optional
              error={save.fieldErrors["pii.bank_ifsc"]}
            />
          </div>
        ) : null}
      </div>
      </FormSection>
      )}
    </FormDialog>
  );
}
