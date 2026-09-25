/**
 * Gap 1/3 — create and edit employee: required-field validation, a pending submit that disables
 * the button and blocks a double submit, a 422 mapped onto `employee_no`, and a successful create
 * that closes the dialog and hands the saved record back to the screen.
 *
 * The form is two steps behind the shared `Stepper` — basic details, then reporting & sensitive
 * details — so every test that reaches the actual save clicks through both.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiError } from "@opsgrids/client";
import type { ProblemJson } from "@opsgrids/client";

import type { EmployeeOut } from "@/api/types";
import { EmployeeForm } from "./EmployeeForm";

const createEmployee = vi.fn();
const updateEmployee = vi.fn();
const getEmployee = vi.fn();

vi.mock("@/api/client", () => ({
  createEmployee: (...args: unknown[]) => createEmployee(...args),
  updateEmployee: (...args: unknown[]) => updateEmployee(...args),
  getEmployee: (...args: unknown[]) => getEmployee(...args),
}));

vi.mock("@/api/orgUnits", () => ({
  getOrgUnitNameById: () => Promise.resolve(new Map()),
}));

vi.mock("@/lib/zone", () => ({
  useZoneLookup: () => ({
    locations: { Bengaluru: "Asia/Kolkata" },
    names: { Bengaluru: "Bengaluru" },
    organizationZone: "Asia/Kolkata",
  }),
}));

function problemWithErrors(detail: string, field: string, message: string): ProblemJson {
  return { detail, errors: [{ field, code: "duplicate", message }] };
}

const CREATED: EmployeeOut = {
  id: "emp-new",
  membership_id: null,
  employee_no: "ATM-0900",
  full_name: "New Person",
  designation: null,
  job_title: null,
  department_id: null,
  location_code: null,
  employment_type: "full_time",
  work_mode: "office",
  date_of_joining: null,
  reporting_manager_id: null,
  dotted_line_manager_id: null,
  status: "probation",
  date_of_exit: null,
  created_at: "2026-09-01T00:00:00Z",
  hierarchy_version: 1,
  pii: null,
};

function renderForm(props: Partial<Parameters<typeof EmployeeForm>[0]> = {}) {
  const onClose = vi.fn();
  const onSaved = vi.fn();
  render(<EmployeeForm employee={null} onClose={onClose} onSaved={onSaved} {...props} />);
  return { onClose, onSaved };
}

/** Fills the basic-details step and moves to reporting & sensitive details. */
async function goToReportingStep(user: ReturnType<typeof userEvent.setup>) {
  await userEvent.type(screen.getByLabelText("Employee number"), "ATM-0900");
  await userEvent.type(screen.getByLabelText("Full name"), "New Person");
  await user.click(screen.getByRole("button", { name: "Continue" }));
  await screen.findByLabelText("Reporting manager");
}

describe("EmployeeForm", () => {
  beforeEach(() => {
    createEmployee.mockReset();
    updateEmployee.mockReset();
    getEmployee.mockReset();
  });

  it("requires a full name before moving past the basic-details step", async () => {
    renderForm();
    await userEvent.type(screen.getByLabelText("Employee number"), "ATM-0900");
    await userEvent.click(screen.getByRole("button", { name: "Continue" }));

    expect(screen.getByText("Full name is required.")).toBeDefined();
    expect(screen.queryByLabelText("Reporting manager")).toBeNull();
    expect(createEmployee).not.toHaveBeenCalled();
  });

  it("moves to reporting & sensitive details on Continue, and the step rail returns to basic details", async () => {
    const user = userEvent.setup();
    renderForm();

    await goToReportingStep(user);
    expect(screen.queryByLabelText("Employee number")).toBeNull();

    await user.click(screen.getByRole("button", { name: /Basic details/ }));
    expect(await screen.findByLabelText("Employee number")).toHaveProperty("value", "ATM-0900");
    expect(screen.getByLabelText("Full name")).toHaveProperty("value", "New Person");
  });

  it("creates an employee, closes the dialog and hands the record back", async () => {
    createEmployee.mockResolvedValue(CREATED);
    const user = userEvent.setup();
    const { onClose, onSaved } = renderForm();

    await goToReportingStep(user);
    await user.click(screen.getByRole("button", { name: "Create employee" }));

    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(CREATED));
    expect(onClose).toHaveBeenCalled();
    const body = createEmployee.mock.calls[0]?.[0] as Record<string, unknown>;
    expect(body.employee_no).toBe("ATM-0900");
    expect(body.full_name).toBe("New Person");
    expect(body.membership_id).toBeUndefined();
  });

  it("disables the submit button while pending and never double-submits", async () => {
    let resolve: (value: EmployeeOut) => void = () => undefined;
    createEmployee.mockReturnValue(new Promise<EmployeeOut>((r) => { resolve = r; }));
    const user = userEvent.setup();
    renderForm();

    await goToReportingStep(user);
    const button = screen.getByRole("button", { name: "Create employee" });
    await user.click(button);
    await user.click(button);

    await waitFor(() => expect(screen.getByRole("button", { name: "Creating…" })).toBeDefined());
    expect(createEmployee).toHaveBeenCalledTimes(1);
    resolve(CREATED);
  });

  it("maps a duplicate employee_no 422 onto the field, jumping back to basic details", async () => {
    createEmployee.mockRejectedValue(
      new ApiError(
        422,
        "duplicate",
        problemWithErrors(
          "An employee with this number already exists",
          "employee_no",
          "This employee number is already in use.",
        ),
      ),
    );
    const user = userEvent.setup();
    renderForm();

    await goToReportingStep(user);
    await user.click(screen.getByRole("button", { name: "Create employee" }));

    await waitFor(() =>
      expect(screen.getByText("This employee number is already in use.")).toBeDefined(),
    );
    expect(screen.getByLabelText("Employee number")).toHaveProperty("value", "ATM-0900");
    expect(screen.getByLabelText("Full name")).toHaveProperty("value", "New Person");
  });

  it("shows the server's sentence on a 403 that belongs to no field", async () => {
    createEmployee.mockRejectedValue(
      new ApiError(403, "forbidden", { detail: "You can't create an employee." }),
    );
    const user = userEvent.setup();
    renderForm();

    await goToReportingStep(user);
    await user.click(screen.getByRole("button", { name: "Create employee" }));

    await waitFor(() => expect(screen.getByText("You can't create an employee.")).toBeDefined());
  });
});
