/**
 * §4.C item 3 — the Correction column is hidden below `md` (`cellClassName`/`headerClassName`
 * both carry `hidden md:table-cell`), so its content must collapse into a line under the status
 * pill instead of disappearing: at 360px the primary action this screen adds — requesting a
 * correction — and every correction state must still be reachable.
 */

import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

import type { AttendanceRecord, Paged } from "@/api/types";
import { AttendancePage } from "./Attendance";

const listAttendanceRoster = vi.fn();
const listAttendance = vi.fn();
const checkOut = vi.fn();

vi.mock("@/api/client", () => ({
  USE_FIXTURES: false,
  listAttendanceRoster: (...args: unknown[]) => listAttendanceRoster(...args),
  listAttendance: (...args: unknown[]) => listAttendance(...args),
  checkOut: (...args: unknown[]) => checkOut(...args),
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
  work_mode: "office",
  status: "absent",
  location_code: "Bengaluru",
  regularization: null,
  allowed_transitions: ["present", "half_day", "regularized"],
};

function pagedOf(items: AttendanceRecord[]): Paged<AttendanceRecord> {
  return { items, total: items.length, page: 1, size: 25, pages: 1 };
}

describe("Attendance — Correction column narrow-width fallback", () => {
  beforeEach(() => {
    listAttendanceRoster.mockReset();
    listAttendance.mockReset();
    checkOut.mockReset();
    listAttendanceRoster.mockResolvedValue(pagedOf([OWN_RECORD]));
    listAttendance.mockResolvedValue({ items: [], total: 0, page: 1, size: 1, pages: 0 });
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

describe("Attendance — why Check in is unavailable", () => {
  beforeEach(() => {
    listAttendanceRoster.mockReset();
    listAttendance.mockReset();
    listAttendanceRoster.mockResolvedValue(pagedOf([OWN_RECORD]));
  });

  it("says so in words, tied to the button, when the day is already checked out", async () => {
    listAttendance.mockResolvedValue({
      items: [{ ...OWN_RECORD, check_in: "2026-08-24T04:00:00Z", check_out_at: "2026-08-24T12:00:00Z" }],
      total: 1,
      page: 1,
      size: 1,
      pages: 1,
    });
    render(<AttendancePage />);

    const reason = await screen.findByText(/Check in is unavailable: Already checked out for the day\./);
    const button = screen.getByRole("button", { name: "Check in" });
    expect(button).toHaveProperty("disabled", true);
    expect(button.getAttribute("aria-describedby")).toBe(reason.id);
  });

  it("shows no reason, and an enabled button, when the person can check in", async () => {
    listAttendance.mockResolvedValue({ items: [], total: 0, page: 1, size: 1, pages: 0 });
    render(<AttendancePage />);

    await waitFor(() => expect(screen.getByRole("button", { name: "Check in" })).toHaveProperty("disabled", false));
    expect(screen.queryByText(/Check in is unavailable/)).toBeNull();
  });
});
