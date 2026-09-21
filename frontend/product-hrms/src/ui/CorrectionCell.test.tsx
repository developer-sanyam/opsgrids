/**
 * §4.C — the correction cell's tri-state routing copy (`approval_opened_at` / `approver_name` /
 * `approver_unassigned`), the rejected row's decider name and comment (design QA gap), and the
 * requester's own reason rendering on their own row only (§9.1 C3, corrected).
 */

import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import type { AttendanceRecord, RegularizationRequestOut } from "@/api/types";
import { CorrectionCell } from "./CorrectionCell";

const ZONE_LOOKUP = {
  locations: { Bengaluru: "Asia/Kolkata" },
  organizationZone: "Asia/Kolkata",
};

const BASE_REG: RegularizationRequestOut = {
  id: "reg-1",
  attendance_id: "att-1",
  employee_id: "emp-other",
  requested_status: "present",
  reason: "Worked from the Pune office, forgot to punch in.",
  status: "pending",
  approval_request_id: "req-1",
  approval_opened_at: "2026-08-24T07:12:00Z",
  approver_employee_id: "emp-priya",
  approver_name: "Priya Nair",
  decided_at: null,
  decision_comment: null,
  decided_by_name: null,
  failure_reason: null,
  created_at: "2026-08-24T07:10:00Z",
  approver_unassigned: false,
  location_code: "Bengaluru",
};

const BASE_RECORD: AttendanceRecord = {
  id: "att-1",
  employee_id: "emp-other",
  employee_no: "ATM-0101",
  full_name: "Ananya Iyer",
  department_id: null,
  job_title: "Engineering",
  work_date: "2026-08-24",
  check_in: null,
  check_out: null,
  worked_minutes: null,
  source: "web",
  work_mode: "office",
  status: "absent",
  location_code: "Bengaluru",
  regularization: BASE_REG,
  allowed_transitions: ["present", "half_day", "regularized"],
};

function renderCell(record: AttendanceRecord, isOwn = false) {
  return render(
    <MemoryRouter>
      <CorrectionCell
        record={record}
        isOwn={isOwn}
        zoneLookup={ZONE_LOOKUP}
        onRequestCorrection={() => {}}
        now={new Date("2026-08-24T07:15:00Z")}
      />
    </MemoryRouter>,
  );
}

describe("CorrectionCell — pending tri-state", () => {
  it("names the routed approver", () => {
    renderCell(BASE_RECORD);
    expect(screen.getByText("Correction pending")).toBeDefined();
    expect(screen.getByText("Waiting on Priya Nair")).toBeDefined();
  });

  it("does not invent a name when the step opened unassigned", () => {
    const record: AttendanceRecord = {
      ...BASE_RECORD,
      regularization: {
        ...BASE_REG,
        approver_employee_id: null,
        approver_name: null,
        approver_unassigned: true,
      },
    };
    renderCell(record);
    expect(screen.getByText("Correction pending")).toBeDefined();
    expect(screen.getByText("Unassigned — no approver found")).toBeDefined();
    expect(screen.queryByText(/Waiting on/)).toBeNull();
  });

  it("does not invent a name when nothing has been routed yet", () => {
    const record: AttendanceRecord = {
      ...BASE_RECORD,
      regularization: {
        ...BASE_REG,
        approval_request_id: null,
        approval_opened_at: null,
        approver_employee_id: null,
        approver_name: null,
        approver_unassigned: null,
      },
    };
    renderCell(record);
    expect(screen.getByText("Waiting to be routed")).toBeDefined();
    expect(screen.queryByText(/Waiting on/)).toBeNull();
    expect(screen.queryByText("Correction pending")).toBeNull();
  });
});

describe("CorrectionCell — rejected", () => {
  it("shows the decider's name and comment", () => {
    const record: AttendanceRecord = {
      ...BASE_RECORD,
      regularization: {
        ...BASE_REG,
        status: "rejected",
        decided_at: "2026-08-24T14:15:00Z",
        decided_by_name: "Kabir Malhotra",
        decision_comment: "You'd already logged this as leave — no change needed.",
      },
    };
    renderCell(record);
    expect(screen.getByText("Correction rejected")).toBeDefined();
    expect(screen.getByText("by Kabir Malhotra")).toBeDefined();
    expect(
      screen.getByText("“You'd already logged this as leave — no change needed.”"),
    ).toBeDefined();
  });

  it("renders no decider line when the decision carries neither", () => {
    const record: AttendanceRecord = {
      ...BASE_RECORD,
      regularization: {
        ...BASE_REG,
        status: "rejected",
        decided_at: "2026-08-24T14:15:00Z",
        decided_by_name: null,
        decision_comment: null,
      },
    };
    renderCell(record);
    expect(screen.getByText("Correction rejected")).toBeDefined();
    expect(screen.queryByText(/^by /)).toBeNull();
  });
});

describe("CorrectionCell — the requester's own reason (§4.C, §9.1 C3)", () => {
  it("renders on the caller's own row", () => {
    renderCell(BASE_RECORD, true);
    expect(
      screen.getByText("Worked from the Pune office, forgot to punch in."),
    ).toBeDefined();
  });

  it("does not render inline on someone else's row", () => {
    renderCell(BASE_RECORD, false);
    expect(
      screen.queryByText("Worked from the Pune office, forgot to punch in."),
    ).toBeNull();
  });

  it("renders on the caller's own rejected row too", () => {
    const record: AttendanceRecord = {
      ...BASE_RECORD,
      regularization: {
        ...BASE_REG,
        status: "rejected",
        decided_at: "2026-08-24T14:15:00Z",
        decided_by_name: "Kabir Malhotra",
        decision_comment: "No change needed.",
      },
    };
    renderCell(record, true);
    expect(
      screen.getByText("Worked from the Pune office, forgot to punch in."),
    ).toBeDefined();
  });
});
