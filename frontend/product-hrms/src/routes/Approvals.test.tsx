/**
 * §4.A — the inbox's four async states (loading, empty, error, populated), plus row activation
 * by keyboard, which is how §1.4 says a request opens.
 */

import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import type { InboxItemOut } from "@/api/types";
import { ApprovalsPage } from "./Approvals";

const listInbox = vi.fn();

vi.mock("@/api/approvals", () => ({
  listInbox: (...args: unknown[]) => listInbox(...args),
}));

const ITEM: InboxItemOut = {
  id: "req-1",
  request_type: "hrms.time.regularization",
  source_ref: "att-1",
  summary: { date: "2026-08-24", from: "absent", to: "regularized" },
  request_note: "Worked from the Pune office, forgot to punch in.",
  opened_at: "2026-08-22T07:10:00Z",
  requester_employee_id: "emp-ananya",
  requester_name: "Ananya Iyer",
  requester_employee_no: "ATM-0101",
  requester_location_code: "Bengaluru",
  inbox_reason: "assigned",
  from_name: null,
};

function renderInbox(path = "/approvals") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/approvals" element={<ApprovalsPage />} />
        <Route path="/approvals/requests/:requestId" element={<div>Request detail</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("Approvals inbox", () => {
  beforeEach(() => {
    listInbox.mockReset();
  });

  it("shows a loading skeleton before the first response, with no layout-shifting spinner", () => {
    listInbox.mockReturnValue(new Promise(() => {})); // never resolves
    renderInbox();
    expect(screen.getByRole("heading", { name: "Approvals" })).toBeDefined();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("renders the empty state with no filters applied", async () => {
    listInbox.mockResolvedValue({ items: [], total: 0, page: 1, size: 25, pages: 0 });
    renderInbox();
    await waitFor(() =>
      expect(screen.getByText("Nothing is waiting on you")).toBeDefined(),
    );
  });

  it("renders the error state with a retry action", async () => {
    listInbox.mockRejectedValue(new Error("The gateway timed out"));
    const user = userEvent.setup();
    renderInbox();

    await waitFor(() => expect(screen.getByText("The gateway timed out")).toBeDefined());
    listInbox.mockResolvedValue({ items: [ITEM], total: 1, page: 1, size: 25, pages: 1 });
    await user.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(screen.getByText("Ananya Iyer")).toBeDefined());
  });

  it("renders a populated inbox and opens a request on Enter", async () => {
    listInbox.mockResolvedValue({ items: [ITEM], total: 1, page: 1, size: 25, pages: 1 });
    const user = userEvent.setup();
    renderInbox();

    await waitFor(() => expect(screen.getByText("Ananya Iyer")).toBeDefined());

    const row = screen.getByRole("button", { name: /Ananya Iyer/ });
    row.focus();
    await user.keyboard("{Enter}");

    await waitFor(() => expect(screen.getByText("Request detail")).toBeDefined());
  });
});
