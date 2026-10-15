/**
 * §4.F "Loaded, large" — a chain beyond 8 rungs collapses to the first 3 and last 2 behind
 * "Show all N levels", the HR fallback rung is never collapsed (it's always the last rung, so
 * always inside the trailing slice), and expanding moves focus to the first newly revealed rung.
 */

import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { ApiError, PermissionProvider } from "@opsgrids/client";

import type { ApprovalChainOut, ChainRungOut, EmployeeOut } from "@/api/types";
import { EmployeeDetailPage } from "./EmployeeDetail";

const getEmployee = vi.fn();
const getChain = vi.fn();
const listEmployeeReports = vi.fn();
const unmaskEmployeePii = vi.fn();

vi.mock("@/api/client", () => ({
  getEmployee: (...args: unknown[]) => getEmployee(...args),
  listEmployeeReports: (...args: unknown[]) => listEmployeeReports(...args),
  unmaskEmployeePii: (...args: unknown[]) => unmaskEmployeePii(...args),
}));

vi.mock("@/api/orgUnits", () => ({
  getOrgUnitNameById: () => Promise.resolve(new Map()),
}));

vi.mock("@/api/approvals", () => ({
  getChain: (...args: unknown[]) => getChain(...args),
}));

const EMPLOYEE: EmployeeOut = {
  id: "emp-ananya",
  membership_id: "mem-1",
  employee_no: "ATM-0101",
  full_name: "Ananya Iyer",
  designation: "Engineer",
  job_title: "Engineering",
  department_id: null,
  location_code: "Bengaluru",
  employment_type: "full_time",
  work_mode: "office",
  date_of_joining: "2022-01-01",
  reporting_manager_id: null,
  dotted_line_manager_id: null,
  hierarchy_version: 1,
  status: "active",
  date_of_exit: null,
  created_at: "2022-01-01T00:00:00Z",
  pii: null,
};

function rung(level: number): ChainRungOut {
  return {
    level,
    employee_id: `emp-rung-${level}`,
    display_name: `Manager ${level}`,
    employee_no: `ATM-0${level}00`,
    skipped: false,
    is_fallback: false,
  };
}

// 10 named rungs plus the HR fallback as the 11th and last — 11 total, above the 8-rung
// collapse threshold.
const LONG_CHAIN: ApprovalChainOut = {
  employee_id: "emp-ananya",
  location_code: "Bengaluru",
  chain: [
    ...Array.from({ length: 10 }, (_, i) => rung(i + 1)),
    {
      level: 11,
      employee_id: null,
      display_name: null,
      employee_no: null,
      skipped: false,
      is_fallback: true,
    },
  ],
  dotted_line_manager: null,
  unresolved: null,
};

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/employees/emp-ananya"]}>
      <Routes>
        <Route path="/employees/:employeeId" element={<EmployeeDetailPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("EmployeeDetail — approval chain collapse (§4.F)", () => {
  beforeEach(() => {
    getEmployee.mockReset();
    getChain.mockReset();
    listEmployeeReports.mockReset();
    unmaskEmployeePii.mockReset();
    getEmployee.mockResolvedValue(EMPLOYEE);
    listEmployeeReports.mockResolvedValue([]);
  });

  it("collapses a chain beyond 8 rungs to the first 3 and last 2, with the HR fallback visible", async () => {
    getChain.mockResolvedValue(LONG_CHAIN);
    renderPage();

    await waitFor(() => expect(screen.getByText("Show all 11 levels")).toBeDefined());

    expect(screen.getByText("Manager 1")).toBeDefined();
    expect(screen.getByText("Manager 2")).toBeDefined();
    expect(screen.getByText("Manager 3")).toBeDefined();
    expect(screen.queryByText("Manager 4")).toBeNull();
    expect(screen.queryByText("Manager 8")).toBeNull();
    expect(screen.queryByText("Manager 9")).toBeNull();
    // The last 2 rungs: Manager 10, then the HR fallback rung — always visible, never collapsed.
    expect(screen.getByText("Manager 10")).toBeDefined();
    expect(screen.getByText("HR")).toBeDefined();
  });

  it("expands to show every rung and moves focus to the first newly revealed one", async () => {
    getChain.mockResolvedValue(LONG_CHAIN);
    const user = userEvent.setup();
    renderPage();

    const toggle = await screen.findByRole("button", { name: "Show all 11 levels" });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    await user.click(toggle);

    await waitFor(() => expect(screen.getByText("Manager 4")).toBeDefined());
    expect(screen.getByText("Manager 5")).toBeDefined();
    expect(screen.getByText("Manager 8")).toBeDefined();
    expect(document.activeElement?.getAttribute("data-rung-level")).toBe("4");
  });

  it("does not collapse a chain of 8 or fewer rungs", async () => {
    getChain.mockResolvedValue({
      ...LONG_CHAIN,
      chain: LONG_CHAIN.chain.slice(0, 8),
    });
    renderPage();

    await waitFor(() => expect(screen.getByText("Manager 1")).toBeDefined());
    expect(screen.getByText("Manager 8")).toBeDefined();
    expect(screen.queryByText(/Show all/)).toBeNull();
  });
});

function renderWithGrants(grants: string[]) {
  return render(
    <PermissionProvider grants={grants}>
      <MemoryRouter initialEntries={["/employees/emp-ananya"]}>
        <Routes>
          <Route path="/employees/:employeeId" element={<EmployeeDetailPage />} />
        </Routes>
      </MemoryRouter>
    </PermissionProvider>,
  );
}

describe("EmployeeDetail — a role without approvals", () => {
  beforeEach(() => {
    getEmployee.mockReset();
    getChain.mockReset();
    listEmployeeReports.mockReset();
    unmaskEmployeePii.mockReset();
    getEmployee.mockResolvedValue(EMPLOYEE);
    listEmployeeReports.mockResolvedValue([]);
  });

  it("leaves the approval chain out, with no permission error, when the server answers 403", async () => {
    getChain.mockRejectedValue(
      new ApiError(403, "Missing permission: core.approval.request.read", {
        title: "Forbidden",
        status: 403,
        detail: "Missing permission: core.approval.request.read",
      }),
    );
    renderPage();

    await screen.findByRole("heading", { name: /Ananya Iyer/ });
    await waitFor(() => expect(getChain).toHaveBeenCalled());
    await waitFor(() => expect(screen.queryByText("Approval chain")).toBeNull());
    expect(screen.queryByText(/Missing permission/)).toBeNull();
    expect(screen.queryByText(/core\.approval/)).toBeNull();
  });

  it("does not even ask for the chain when the grants say the role has no approvals", async () => {
    renderWithGrants(["hrms.people.employee.read:ORG"]);

    await screen.findByRole("heading", { name: /Ananya Iyer/ });
    expect(screen.queryByText("Approval chain")).toBeNull();
    expect(getChain).not.toHaveBeenCalled();
  });

  it("shows the chain to a role that has approvals", async () => {
    getChain.mockResolvedValue(LONG_CHAIN);
    renderWithGrants(["core.approval.request.read:ORG"]);
    expect(await screen.findByText("Approval chain")).toBeDefined();
  });
});
