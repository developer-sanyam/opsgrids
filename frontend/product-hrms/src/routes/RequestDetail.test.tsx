/**
 * §3.2, §4.B — deciding from the detail screen: the happy path announces and returns to the
 * inbox, and a 409 (someone else decided first) renders inline with the decider named.
 */

import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";

import { ApiError } from "@opsgrids/client";
import type { RequestDetailOut } from "@/api/types";
import { RequestDetailPage } from "./RequestDetail";

const getRequestDetail = vi.fn();
const decideRequest = vi.fn();

vi.mock("@/api/approvals", () => ({
  getRequestDetail: (...args: unknown[]) => getRequestDetail(...args),
  decideRequest: (...args: unknown[]) => decideRequest(...args),
}));

const PENDING: RequestDetailOut = {
  id: "req-1",
  request_type: "hrms.time.regularization",
  source_ref: "att-1",
  summary: { date: "2026-08-24", from: "absent", to: "regularized" },
  request_note: "Worked from the Pune office, forgot to punch in.",
  status: "pending",
  opened_at: "2026-08-22T07:10:00Z",
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
  steps: [
    {
      level: 1,
      reason: "assigned",
      outcome: "open",
      approver_employee_id: "emp-sneha",
      approver_name: "Sneha Kulkarni",
      acting_for_employee_id: null,
      acting_for_name: null,
      opened_at: "2026-08-22T07:10:00Z",
      closed_at: null,
      due_at: null,
    },
  ],
};

function InboxProbe() {
  const location = useLocation();
  const state = location.state as { announce?: string } | null;
  return <div>Inbox — {state?.announce ?? "no announcement"}</div>;
}

function renderDetail() {
  return render(
    <MemoryRouter initialEntries={["/approvals/requests/req-1"]}>
      <Routes>
        <Route path="/approvals/requests/:requestId" element={<RequestDetailPage />} />
        <Route path="/approvals" element={<InboxProbe />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("RequestDetail — deciding", () => {
  beforeEach(() => {
    getRequestDetail.mockReset();
    decideRequest.mockReset();
  });

  it("approves, announces the outcome, and returns to the inbox", async () => {
    getRequestDetail.mockResolvedValue(PENDING);
    decideRequest.mockResolvedValue({
      ...PENDING,
      status: "approved",
      application_state: "applied",
      decided_by_employee_id: "emp-sneha",
      decided_by_name: "Sneha Kulkarni",
      decided_at: "2026-08-24T09:00:00Z",
    });
    const user = userEvent.setup();
    renderDetail();

    await waitFor(() => expect(screen.getByRole("button", { name: "Approve" })).toBeDefined());
    await user.click(screen.getByRole("button", { name: "Approve" }));
    const dialog = await screen.findByRole("dialog", { name: "Approve this correction?" });
    await user.click(within(dialog).getByRole("button", { name: "Approve" }));

    await waitFor(() =>
      expect(
        screen.getByText(
          "Inbox — Approved Ananya Iyer's attendance correction for 24 Aug 2026.",
        ),
      ).toBeDefined(),
    );
  });

  it("renders the 409 conflict inline, naming the decider, without navigating away", async () => {
    getRequestDetail
      .mockResolvedValueOnce(PENDING)
      .mockResolvedValueOnce({
        ...PENDING,
        status: "approved",
        application_state: "applied",
        decided_by_employee_id: "emp-marcus",
        decided_by_name: "Marcus Feng",
        decided_at: "2026-08-24T09:00:00Z",
        inbox_reason: null,
      });
    decideRequest.mockRejectedValue(new ApiError(409, "This request was already approved", null));
    const user = userEvent.setup();
    renderDetail();

    await waitFor(() => expect(screen.getByRole("button", { name: "Approve" })).toBeDefined());
    await user.click(screen.getByRole("button", { name: "Approve" }));
    const dialog = await screen.findByRole("dialog", { name: "Approve this correction?" });
    await user.click(within(dialog).getByRole("button", { name: "Approve" }));

    await waitFor(() => expect(screen.getByText("Already decided")).toBeDefined());
    expect(screen.getByText(/Marcus Feng approved this on/)).toBeDefined();
    expect(screen.queryByText(/^Inbox/)).toBeNull();
  });
});

describe("RequestDetail — the due line (slice 4, §2.11, §4.B)", () => {
  beforeEach(() => {
    getRequestDetail.mockReset();
    decideRequest.mockReset();
  });

  it("a step with no due_at renders 'No due time' — never a fabricated deadline", async () => {
    getRequestDetail.mockResolvedValue(PENDING); // PENDING's own open step has `due_at: null`.
    renderDetail();

    await waitFor(() => expect(screen.getByText("Waiting on")).toBeDefined());
    expect(screen.getByText("No due time")).toBeDefined();
    expect(screen.queryByText(/^Due /)).toBeNull();
    expect(screen.queryByText(/^Overdue/)).toBeNull();
  });

  it("an overdue step is marked, in words, not colour alone", async () => {
    getRequestDetail.mockResolvedValue({
      ...PENDING,
      steps: [
        {
          ...PENDING.steps[0]!,
          due_at: "2020-01-01T00:00:00Z", // Long past — overdue no matter when this test runs.
        },
      ],
    });
    renderDetail();

    await waitFor(() => expect(screen.getByText(/^Overdue by /)).toBeDefined());
    expect(screen.queryByText("No due time")).toBeNull();
  });

  it("a future due_at reads 'Due …' with both the absolute time and a relative one", async () => {
    getRequestDetail.mockResolvedValue({
      ...PENDING,
      steps: [
        {
          ...PENDING.steps[0]!,
          // A couple of minutes past the 6-hour mark, so rounding in `formatDuration` (which
          // floors) can never tip this into "5 hours" no matter how long the test takes to run.
          due_at: new Date(Date.now() + (6 * 60 + 2) * 60 * 1000).toISOString(),
        },
      ],
    });
    renderDetail();

    await waitFor(() => expect(screen.getByText(/^Due /)).toBeDefined());
    expect(screen.getByText(/in 6 hours/)).toBeDefined();
  });

  it("a decided request has no open step, so no due line renders at all", async () => {
    getRequestDetail.mockResolvedValue({
      ...PENDING,
      status: "approved",
      application_state: "applied",
      decided_by_employee_id: "emp-sneha",
      decided_by_name: "Sneha Kulkarni",
      decided_at: "2026-08-24T09:00:00Z",
      steps: [
        {
          ...PENDING.steps[0]!,
          outcome: "approved",
          closed_at: "2026-08-24T09:00:00Z",
          due_at: "2026-08-24T12:00:00Z",
        },
      ],
    });
    renderDetail();

    await waitFor(() => expect(screen.getByText("Approved")).toBeDefined());
    expect(screen.queryByText("No due time")).toBeNull();
    expect(screen.queryByText(/^Due /)).toBeNull();
    expect(screen.queryByText(/^Overdue/)).toBeNull();
  });
});
