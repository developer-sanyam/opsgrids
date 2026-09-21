/**
 * §3.1 — every failure branch the correction dialog owns: client validation, the server's 409
 * (already pending) and 422s (illegal transition, blank reason), and that nothing typed is lost
 * on failure.
 */

import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiError } from "@opsgrids/client";
import type { ProblemJson } from "@opsgrids/client";
import { Announcer } from "@opsgrids/ui";

import type { AttendanceRecord } from "@/api/types";
import type { FieldError } from "@/lib/problem";
import { RequestCorrectionDialog } from "./RequestCorrectionDialog";

const requestRegularization = vi.fn();

vi.mock("@/api/client", () => ({
  requestRegularization: (...args: unknown[]) => requestRegularization(...args),
}));

const RECORD: AttendanceRecord = {
  id: "att-1",
  employee_id: "emp-1",
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

const ZONE_LOOKUP = {
  locations: { Bengaluru: "Asia/Kolkata" },
  organizationZone: "Asia/Kolkata",
};

function renderDialog(onSettled = vi.fn()) {
  const onClose = vi.fn();
  render(
    <Announcer>
      <RequestCorrectionDialog
        open
        onClose={onClose}
        record={RECORD}
        zoneLookup={ZONE_LOOKUP}
        onSettled={onSettled}
      />
    </Announcer>,
  );
  return { onClose, onSettled };
}

describe("RequestCorrectionDialog", () => {
  beforeEach(() => {
    requestRegularization.mockReset();
    vi.spyOn(navigator, "onLine", "get").mockReturnValue(true);
  });

  it("won't submit without a chosen status, and moves focus to the radio group (a11y)", async () => {
    const user = userEvent.setup();
    renderDialog();
    await user.click(screen.getByRole("button", { name: "Send request" }));
    expect(screen.getByText("Choose what the day should say.")).toBeDefined();
    expect(requestRegularization).not.toHaveBeenCalled();
    expect(document.activeElement).toBe(screen.getAllByRole("radio")[0]);
  });

  it("won't submit a blank reason, and moves focus to the reason field (a11y)", async () => {
    const user = userEvent.setup();
    renderDialog();
    await user.click(screen.getByRole("radio", { name: /Present/ }));
    await user.click(screen.getByRole("button", { name: "Send request" }));
    expect(screen.getByText("Reason cannot be blank")).toBeDefined();
    expect(requestRegularization).not.toHaveBeenCalled();
    expect(document.activeElement).toBe(screen.getByLabelText("Why does this need changing?"));
  });

  it("submits the chosen status and typed reason", async () => {
    requestRegularization.mockResolvedValue({
      id: "reg-1",
      attendance_id: "att-1",
      employee_id: "emp-1",
      requested_status: "present",
      reason: "Was onsite all day.",
      status: "pending",
      approval_request_id: null,
      approval_opened_at: null,
      approver_employee_id: null,
      approver_name: null,
      decided_at: null,
      decision_comment: null,
      decided_by_name: null,
      failure_reason: null,
      created_at: "2026-08-24T10:00:00Z",
      approver_unassigned: null,
      location_code: "Bengaluru",
    });
    const user = userEvent.setup();
    const { onSettled } = renderDialog();

    await user.click(screen.getByRole("radio", { name: /Present/ }));
    await user.type(screen.getByLabelText("Why does this need changing?"), "Was onsite all day.");
    await user.click(screen.getByRole("button", { name: "Send request" }));

    await waitFor(() => expect(onSettled).toHaveBeenCalledWith({ kind: "created" }));
    expect(requestRegularization).toHaveBeenCalledWith("att-1", {
      requested_status: "present",
      reason: "Was onsite all day.",
    });
  });

  it("announces 'Waiting to be routed' when the response carries no approval_opened_at yet", async () => {
    requestRegularization.mockResolvedValue({
      id: "reg-1",
      attendance_id: "att-1",
      employee_id: "emp-1",
      requested_status: "present",
      reason: "Was onsite all day.",
      status: "pending",
      approval_request_id: null,
      approval_opened_at: null,
      approver_employee_id: null,
      approver_name: null,
      decided_at: null,
      decision_comment: null,
      decided_by_name: null,
      failure_reason: null,
      created_at: "2026-08-24T10:00:00Z",
      approver_unassigned: null,
      location_code: "Bengaluru",
    });
    const user = userEvent.setup();
    renderDialog();

    await user.click(screen.getByRole("radio", { name: /Present/ }));
    await user.type(screen.getByLabelText("Why does this need changing?"), "Was onsite all day.");
    await user.click(screen.getByRole("button", { name: "Send request" }));

    await waitFor(() =>
      expect(
        screen.getByText("Correction requested for 24 Aug 2026. Waiting to be routed."),
      ).toBeDefined(),
    );
  });

  it("names the approver — matching CorrectionCell — once the request comes back already routed", async () => {
    requestRegularization.mockResolvedValue({
      id: "reg-1",
      attendance_id: "att-1",
      employee_id: "emp-1",
      requested_status: "present",
      reason: "Was onsite all day.",
      status: "pending",
      approval_request_id: "req-1",
      approval_opened_at: "2026-08-24T10:00:05Z",
      approver_employee_id: "emp-priya",
      approver_name: "Priya Nair",
      decided_at: null,
      decision_comment: null,
      decided_by_name: null,
      failure_reason: null,
      created_at: "2026-08-24T10:00:00Z",
      approver_unassigned: false,
      location_code: "Bengaluru",
    });
    const user = userEvent.setup();
    renderDialog();

    await user.click(screen.getByRole("radio", { name: /Present/ }));
    await user.type(screen.getByLabelText("Why does this need changing?"), "Was onsite all day.");
    await user.click(screen.getByRole("button", { name: "Send request" }));

    await waitFor(() =>
      expect(
        screen.getByText("Correction requested for 24 Aug 2026. Waiting on Priya Nair."),
      ).toBeDefined(),
    );
  });

  it("falls back to 'Correction pending' when routed but the step opened with no approver name", async () => {
    requestRegularization.mockResolvedValue({
      id: "reg-1",
      attendance_id: "att-1",
      employee_id: "emp-1",
      requested_status: "present",
      reason: "Was onsite all day.",
      status: "pending",
      approval_request_id: "req-1",
      approval_opened_at: "2026-08-24T10:00:05Z",
      approver_employee_id: null,
      approver_name: null,
      decided_at: null,
      decision_comment: null,
      decided_by_name: null,
      failure_reason: null,
      created_at: "2026-08-24T10:00:00Z",
      approver_unassigned: true,
      location_code: "Bengaluru",
    });
    const user = userEvent.setup();
    renderDialog();

    await user.click(screen.getByRole("radio", { name: /Present/ }));
    await user.type(screen.getByLabelText("Why does this need changing?"), "Was onsite all day.");
    await user.click(screen.getByRole("button", { name: "Send request" }));

    await waitFor(() =>
      expect(
        screen.getByText("Correction requested for 24 Aug 2026. Correction pending."),
      ).toBeDefined(),
    );
  });

  it("renders the server's 409 and preserves nothing typed being lost — the actions collapse to Close", async () => {
    requestRegularization.mockRejectedValue(
      new ApiError(409, "already pending", {
        detail: "A regularization request for this record is already pending",
      }),
    );
    const user = userEvent.setup();
    renderDialog();

    await user.click(screen.getByRole("radio", { name: /Present/ }));
    await user.type(screen.getByLabelText("Why does this need changing?"), "Was onsite all day.");
    await user.click(screen.getByRole("button", { name: "Send request" }));

    await waitFor(() =>
      expect(
        screen.getByText("There's already a correction waiting on this day."),
      ).toBeDefined(),
    );
    expect(screen.queryByRole("button", { name: "Send request" })).toBeNull();
    expect(screen.getByRole("button", { name: "Close" })).toBeDefined();
  });

  it("renders a 422 field error under the reason field, verbatim, keeps the typed text, and moves focus there (a11y)", async () => {
    const problem: ProblemJson & { errors: FieldError[] } = {
      detail: "Request payload failed validation",
      errors: [{ field: "reason", code: "string_too_short", message: "Reason cannot be blank" }],
    };
    requestRegularization.mockRejectedValue(new ApiError(422, "validation failed", problem));
    const user = userEvent.setup();
    renderDialog();

    await user.click(screen.getByRole("radio", { name: /Present/ }));
    const reasonField = screen.getByLabelText("Why does this need changing?");
    await user.type(reasonField, "x");
    await user.click(screen.getByRole("button", { name: "Send request" }));

    await waitFor(() => expect(screen.getByText("Reason cannot be blank")).toBeDefined());
    expect((reasonField as HTMLTextAreaElement).value).toBe("x");
    expect(document.activeElement).toBe(reasonField);
  });

  it("renders a business-rule 422 under the radio group, verbatim from the server, and moves focus there (a11y)", async () => {
    requestRegularization.mockRejectedValue(
      new ApiError(422, "illegal transition", {
        detail: "An attendance record cannot move from 'absent' to 'present'",
      }),
    );
    const user = userEvent.setup();
    renderDialog();

    await user.click(screen.getByRole("radio", { name: /Present/ }));
    await user.type(screen.getByLabelText("Why does this need changing?"), "Was onsite all day.");
    await user.click(screen.getByRole("button", { name: "Send request" }));

    await waitFor(() =>
      expect(
        screen.getByText("An attendance record cannot move from 'absent' to 'present'"),
      ).toBeDefined(),
    );
    expect(document.activeElement).toBe(screen.getByRole("radio", { name: /Present/ }));
  });

  it("blocks submit while offline, without losing what was typed", async () => {
    vi.spyOn(navigator, "onLine", "get").mockReturnValue(false);
    const user = userEvent.setup();
    renderDialog();

    await user.click(screen.getByRole("radio", { name: /Present/ }));
    await user.type(screen.getByLabelText("Why does this need changing?"), "Was onsite all day.");
    await user.click(screen.getByRole("button", { name: "Send request" }));

    expect(
      screen.getByText("You're offline. Reconnect to send this request."),
    ).toBeDefined();
    expect(requestRegularization).not.toHaveBeenCalled();
    expect(
      (screen.getByLabelText("Why does this need changing?") as HTMLTextAreaElement).value,
    ).toBe("Was onsite all day.");
  });

  it("focuses the first radio on open (§4.C accessibility)", () => {
    renderDialog();
    expect(document.activeElement).toBe(screen.getAllByRole("radio")[0]);
  });
});
