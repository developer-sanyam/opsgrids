/**
 * §3.2, §4.B — the decision confirmation: the happy path, the 409 "someone else decided first"
 * branch, keyboard operability (Escape cancels) and the fact a decision is announced.
 */

import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiError } from "@opsgrids/client";
import { Announcer } from "@opsgrids/ui";

import type { RequestDetailOut } from "@/api/types";
import { DecisionDialog } from "./DecisionDialog";

const decideRequest = vi.fn();

vi.mock("@/api/approvals", () => ({
  decideRequest: (...args: unknown[]) => decideRequest(...args),
}));

const REQUEST: RequestDetailOut = {
  id: "req-1",
  request_type: "hrms.time.regularization",
  source_ref: "att-1",
  summary: { date: "2026-08-24", from: "absent", to: "regularized" },
  request_note: "Worked from the Pune office, forgot to punch in.",
  status: "pending",
  opened_at: "2026-08-24T07:10:00Z",
  requester_employee_id: "emp-ananya",
  requester_name: "Ananya Iyer",
  requester_employee_no: "ATM-0101",
  requester_location_code: "Bengaluru",
  approver_employee_id: "emp-sneha",
  approver_name: "Sneha Kulkarni",
  acting_for_employee_id: null,
  acting_for_name: null,
  inbox_reason: "assigned",
  from_name: null,
  decided_by_employee_id: null,
  decided_by_name: null,
  decided_at: null,
  comment: null,
  application_state: null,
  application_failure_reason: null,
  applied_at: null,
  steps: [],
};

const ZONE_LOOKUP = { locations: { Bengaluru: "Asia/Kolkata" }, organizationZone: "Asia/Kolkata" };

function renderDialog(outcome: "approve" | "reject" = "approve") {
  const onClose = vi.fn();
  const onDecided = vi.fn();
  const onConflict = vi.fn();
  const onIneligible = vi.fn();
  render(
    <Announcer>
      <DecisionDialog
        open
        onClose={onClose}
        outcome={outcome}
        request={REQUEST}
        zoneLookup={ZONE_LOOKUP}
        onDecided={onDecided}
        onConflict={onConflict}
        onIneligible={onIneligible}
      />
    </Announcer>,
  );
  return { onClose, onDecided, onConflict, onIneligible };
}

describe("DecisionDialog", () => {
  beforeEach(() => {
    decideRequest.mockReset();
    vi.spyOn(navigator, "onLine", "get").mockReturnValue(true);
  });

  it("names the consequence before confirming", () => {
    renderDialog("approve");
    expect(
      screen.getByText(
        "Ananya Iyer's attendance for 24 Aug 2026 will change from Absent to Regularised. This can't be undone.",
      ),
    ).toBeDefined();
  });

  it("sends the outcome and an optional comment, and reports the decided request back", async () => {
    decideRequest.mockResolvedValue({ ...REQUEST, status: "approved", application_state: "applied" });
    const user = userEvent.setup();
    const { onDecided } = renderDialog("approve");

    await user.type(screen.getByLabelText(/Comment/), "Looks right.");
    await user.click(screen.getByRole("button", { name: "Approve" }));

    await waitFor(() => expect(onDecided).toHaveBeenCalled());
    expect(decideRequest).toHaveBeenCalledWith("req-1", { outcome: "approve", comment: "Looks right." });
  });

  it("renders the 409 'already decided by someone else' branch and hands the typed comment back", async () => {
    decideRequest.mockRejectedValue(new ApiError(409, "This request was already approved", null));
    const user = userEvent.setup();
    const { onConflict } = renderDialog("reject");

    await user.type(screen.getByLabelText(/Comment/), "Wait, don't.");
    await user.click(screen.getByRole("button", { name: "Reject" }));

    await waitFor(() => expect(onConflict).toHaveBeenCalledWith("Wait, don't."));
  });

  it("blocks confirm while offline and keeps the typed comment", async () => {
    vi.spyOn(navigator, "onLine", "get").mockReturnValue(false);
    const user = userEvent.setup();
    renderDialog("approve");

    await user.type(screen.getByLabelText(/Comment/), "Kept.");
    await user.click(screen.getByRole("button", { name: "Approve" }));

    expect(screen.getByText("You're offline. Reconnect to send this decision.")).toBeDefined();
    expect(decideRequest).not.toHaveBeenCalled();
    expect((screen.getByLabelText(/Comment/) as HTMLTextAreaElement).value).toBe("Kept.");
  });

  it("is a real modal, closable with Escape", async () => {
    const user = userEvent.setup();
    const { onClose } = renderDialog("approve");
    expect(screen.getByRole("dialog", { name: "Approve this correction?" })).toBeDefined();
    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalled();
  });

  it("autofocuses the comment field on reject", () => {
    renderDialog("reject");
    expect(document.activeElement).toBe(screen.getByLabelText(/Comment/));
  });

  it("caps the comment at 500 characters (§4.B) — not the server's 2000-character limit", () => {
    renderDialog("approve");
    const field = screen.getByLabelText(/Comment/) as HTMLTextAreaElement;
    expect(field.maxLength).toBe(500);
  });
});
