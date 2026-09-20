/**
 * §4.C item 3 — the Correction column is hidden below `md` (`cellClassName`/`headerClassName`
 * both carry `hidden md:table-cell`), so its content must collapse into a line under the status
 * pill instead of disappearing: at 360px the primary action this screen adds — requesting a
 * correction — and every correction state must still be reachable.
 */

import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

import type { AttendanceRecord } from "@/api/types";
import { AttendancePage } from "./Attendance";

const listAttendance = vi.fn();

vi.mock("@/api/client", () => ({
  ATTENDANCE_IS_SAMPLE: false,
  listAttendance: (...args: unknown[]) => listAttendance(...args),
}));

vi.mock("@/lib/subject", () => ({
  getMyEmployeeId: () => "emp-own",
}));

vi.mock("@/lib/zone", () => ({
  useZoneLookup: () => ({ locations: { Bengaluru: "Asia/Kolkata" }, organizationZone: "Asia/Kolkata" }),
}));

const OWN_RECORD: AttendanceRecord = {
  id: "att-own",
  employee_id: "emp-own",
  employee_no: "ATM-0301",
  full_name: "Sneha Kulkarni",
  department_id: null,
  job_title: "HR",
  work_date: "2026-08-24",
  check_in: null,
  check_out: null,
  worked_minutes: null,
  source: "web",
  status: "absent",
  location_code: "Bengaluru",
  regularization: null,
  allowed_transitions: ["present", "half_day", "regularized"],
};

describe("Attendance — Correction column narrow-width fallback", () => {
  beforeEach(() => {
    listAttendance.mockReset();
    listAttendance.mockResolvedValue([OWN_RECORD]);
  });

  it("renders 'Request a correction' both in the (md+) Correction column and as a line under the status pill", async () => {
    render(<AttendancePage />);

    await waitFor(() => expect(screen.getByText("Sneha Kulkarni")).toBeDefined());

    const buttons = screen.getAllByRole("button", { name: "Request a correction" });
    // One lives in the Correction column (`hidden md:table-cell` — visible at md+), the other
    // under the status pill inside a `md:hidden` wrapper (visible below md). Neither is ever
    // both-hidden, so the action survives at every width, including 360px.
    expect(buttons).toHaveLength(2);

    const belowMdCopy = buttons.find((b) => b.closest(".md\\:hidden") !== null);
    expect(belowMdCopy).toBeDefined();

    const columnCopy = buttons.find((b) => b.closest("td")?.className.includes("md:table-cell"));
    expect(columnCopy).toBeDefined();
  });
});
