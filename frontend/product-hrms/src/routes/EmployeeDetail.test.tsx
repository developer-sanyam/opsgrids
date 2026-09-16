/**
 * §4.F "Loaded, large" — a chain beyond 8 rungs collapses to the first 3 and last 2 behind
 * "Show all N levels", the HR fallback rung is never collapsed (it's always the last rung, so
 * always inside the trailing slice), and expanding moves focus to the first newly revealed rung.
 */

import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import type { ApprovalChainOut, ChainRungOut, EmployeeOut } from "@/api/types";
import { EmployeeDetailPage } from "./EmployeeDetail";

const getEmployee = vi.fn();
const getChain = vi.fn();

vi.mock("@/api/client", () => ({
  getEmployee: (...args: unknown[]) => getEmployee(...args),
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
    getEmployee.mockResolvedValue(EMPLOYEE);
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
