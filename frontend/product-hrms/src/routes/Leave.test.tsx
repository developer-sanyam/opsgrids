/**
 * `LeavePage` — loading, success (balances + requests), the apply dialog and the cancel action.
 * Mirrors `Attendance.test.tsx`'s shape: mock the api module directly, no router needed since
 * this screen never navigates.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { PermissionProvider } from "@opsgrids/client";

import type { LeaveBalanceOut, LeaveRequestOut, LeaveTypeOut, Paged } from "@/api/types";
import { LeavePage } from "./Leave";

/** The self-service grant this screen's Apply/Cancel controls gate on (`useCan`/`<Can>` fail
 * closed with no `PermissionProvider` in the tree, the same "hides UI, is not enforcement"
 * discipline `ApprovalSettings.test.tsx` renders under in this same product). */
function renderWithWriteAccess() {
  return render(
    <PermissionProvider grants={["hrms.time.leave_request.write:OWN"]}>
      <LeavePage />
    </PermissionProvider>,
  );
}

const listLeaveBalances = vi.fn();
const listLeaveRequests = vi.fn();
const listLeaveTypes = vi.fn();
const applyForLeave = vi.fn();
const cancelLeaveRequest = vi.fn();

vi.mock("@/api/leave", () => ({
  listLeaveBalances: (...args: unknown[]) => listLeaveBalances(...args),
  listLeaveRequests: (...args: unknown[]) => listLeaveRequests(...args),
  listLeaveTypes: (...args: unknown[]) => listLeaveTypes(...args),
  applyForLeave: (...args: unknown[]) => applyForLeave(...args),
  cancelLeaveRequest: (...args: unknown[]) => cancelLeaveRequest(...args),
  forceCancelLeaveRequest: vi.fn(),
}));

vi.mock("@/lib/subject", () => ({
  getMyEmployeeId: () => "emp-own",
}));

const BALANCES: LeaveBalanceOut[] = [
  { employee_id: "emp-own", leave_type_id: "lt-el", leave_type_code: "EL", balance_half_days: 20 },
  { employee_id: "emp-own", leave_type_id: "lt-sl", leave_type_code: "SL", balance_half_days: 24 },
];

const LEAVE_TYPES: LeaveTypeOut[] = [
  {
    id: "lt-el",
    code: "EL",
    name: "Earned leave",
    is_paid: true,
    annual_quota_half_days: 48,
    monthly_accrual_half_days: null,
    carry_forward_cap_half_days: null,
    active: true,
    created_at: "2026-01-01T00:00:00Z",
  },
];

const PENDING_REQUEST: LeaveRequestOut = {
  id: "req-1",
  employee_id: "emp-own",
  leave_type_id: "lt-el",
  start_date: "2026-09-21",
  end_date: "2026-09-22",
  half_day_start: false,
  half_day_end: false,
  total_half_days: 4,
  reason: "Family function",
  status: "pending",
  approval_request_id: null,
  approval_opened_at: null,
  approver_employee_id: null,
  approver_name: null,
  approver_unassigned: null,
  decided_at: null,
  decision_comment: null,
  decided_by_name: null,
  failure_reason: null,
  cancelled_at: null,
  cancellation_reason: null,
  created_at: "2026-09-01T00:00:00Z",
};

function pagedOf(items: LeaveRequestOut[]): Paged<LeaveRequestOut> {
  return { items, total: items.length, page: 1, size: 25, pages: 1 };
}

describe("LeavePage", () => {
  beforeEach(() => {
    listLeaveBalances.mockReset();
    listLeaveRequests.mockReset();
    listLeaveTypes.mockReset();
    applyForLeave.mockReset();
    cancelLeaveRequest.mockReset();
    listLeaveBalances.mockResolvedValue(BALANCES);
    listLeaveRequests.mockResolvedValue(pagedOf([PENDING_REQUEST]));
    listLeaveTypes.mockResolvedValue(pagedOf(LEAVE_TYPES as unknown as LeaveRequestOut[]));
  });

  it("renders balances and the requester's own leave requests", async () => {
    render(<LeavePage />);

    await waitFor(() => expect(screen.getByText("EL")).toBeDefined());
    expect(screen.getByText("SL")).toBeDefined();
    expect(screen.getByText("Family function")).toBeDefined();
    expect(screen.getByText("Pending")).toBeDefined();
  });

  it("opens the apply dialog and submits a new request", async () => {
    const user = userEvent.setup();
    applyForLeave.mockResolvedValue({
      ...PENDING_REQUEST,
      id: "req-2",
      reason: "Doctor visit",
    });

    renderWithWriteAccess();
    await waitFor(() => expect(screen.getByText("Family function")).toBeDefined());

    await user.click(screen.getByRole("button", { name: "Apply for leave" }));
    await waitFor(() => expect(screen.getByRole("dialog")).toBeDefined());

    await user.type(screen.getByLabelText("Start date"), "2026-10-01");
    await user.type(screen.getByLabelText("End date"), "2026-10-01");
    await user.type(screen.getByLabelText("Reason"), "Doctor visit");
    await user.click(screen.getByRole("button", { name: "Submit request" }));

    await waitFor(() => expect(applyForLeave).toHaveBeenCalledTimes(1));
    expect(applyForLeave.mock.calls[0]?.[0]).toMatchObject({
      leave_type_id: "lt-el",
      start_date: "2026-10-01",
      end_date: "2026-10-01",
      reason: "Doctor visit",
    });
  });

  it("cancels a pending request", async () => {
    const user = userEvent.setup();
    cancelLeaveRequest.mockResolvedValue({
      ...PENDING_REQUEST,
      status: "cancelled",
      cancelled_at: "2026-09-05T00:00:00Z",
    });

    renderWithWriteAccess();
    await waitFor(() => expect(screen.getByText("Family function")).toBeDefined());

    // A single visible row action renders inline (no overflow menu) — its accessible name is
    // `${label} ${rowLabel}` (`RowActions`'s own convention), so it reads as more than "Cancel".
    await user.click(screen.getByRole("button", { name: /^Cancel leave request for/ }));
    await user.click(await screen.findByRole("button", { name: "Cancel request" }));

    await waitFor(() => expect(cancelLeaveRequest).toHaveBeenCalledWith("req-1", { reason: null }));
  });

  it("renders an empty state when the employee has no leave requests", async () => {
    listLeaveRequests.mockResolvedValue(pagedOf([]));
    render(<LeavePage />);
    await waitFor(() =>
      expect(screen.getByText("You haven't applied for leave yet")).toBeDefined(),
    );
  });
});
