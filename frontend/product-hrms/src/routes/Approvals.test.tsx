/**
 * §4.A — the inbox's four async states (loading, empty, error, populated), plus row activation
 * by keyboard, which is how §1.4 says a request opens.
 */

import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
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
  due_at: null,
};

/** Read against the real clock, the way the screen does — a due time is only meaningful relative
 *  to "now", so these are offsets rather than the fixed dates every other field here uses. */
function hoursFromNow(hours: number): string {
  return new Date(Date.now() + hours * 60 * 60 * 1000).toISOString();
}

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

  it("renders both escalation chips with the right names, and filters to 'Escalated to me'", async () => {
    const escalatedToMe: InboxItemOut = {
      ...ITEM,
      id: "req-escalated-to-me",
      requester_name: "Rohan Mehta",
      inbox_reason: "escalated_to_me",
      from_name: "Priya Nair",
    };
    const escalatedAway: InboxItemOut = {
      ...ITEM,
      id: "req-escalated-away",
      requester_name: "Daniel Okafor",
      inbox_reason: "escalated_away",
      from_name: "Marcus Feng",
    };
    listInbox.mockResolvedValue({
      items: [ITEM, escalatedToMe, escalatedAway],
      total: 3,
      page: 1,
      size: 25,
      pages: 1,
    });
    const user = userEvent.setup();
    renderInbox();

    await waitFor(() => expect(screen.getByText("Rohan Mehta")).toBeDefined());
    // The chip renders twice by design — once in the "Why" column, once under the requester's
    // name for the `< 768` layout (§4.A) — so JSDOM, which applies no responsive `display: none`,
    // sees both.
    expect(screen.getAllByText("Escalated to you from Priya Nair").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Still yours — now with Marcus Feng").length).toBeGreaterThan(0);

    await user.click(screen.getByRole("button", { name: "Escalated to me" }));
    expect(screen.getByText("Rohan Mehta")).toBeDefined();
    expect(screen.queryByText("Daniel Okafor")).toBeNull();
    expect(screen.queryByText("Ananya Iyer")).toBeNull();
  });
});

/**
 * §4.A's Due column and `Overdue` filter (slice 4). The rule under test throughout is §2.11's: the
 * screen renders the `due_at` the server sent and never derives one — so a `null` reads as the
 * modelled "no deadline" state, and nothing anywhere turns an `opened_at` into a deadline.
 */
describe("Approvals inbox — deadlines", () => {
  beforeEach(() => {
    listInbox.mockReset();
  });

  const WITH_DUE: InboxItemOut = {
    ...ITEM,
    id: "req-due",
    requester_name: "Emily Watson",
    due_at: hoursFromNow(6.5),
  };
  const NO_DUE: InboxItemOut = {
    ...ITEM,
    id: "req-no-due",
    requester_name: "Ananya Iyer",
    inbox_reason: "unassigned",
    due_at: null,
  };
  const OVERDUE: InboxItemOut = {
    ...ITEM,
    id: "req-overdue",
    requester_name: "Kabir Shah",
    due_at: hoursFromNow(-5.5),
  };

  it("renders the server's deadline as a duration, and a null as 'No due time'", async () => {
    listInbox.mockResolvedValue({
      items: [WITH_DUE, NO_DUE],
      total: 2,
      page: 1,
      size: 25,
      pages: 1,
    });
    renderInbox();

    await waitFor(() => expect(screen.getByText("Emily Watson")).toBeDefined());
    expect(screen.getByRole("columnheader", { name: "Due" })).toBeDefined();
    expect(screen.getByText("Due in 6 hours")).toBeDefined();
    // Not a blank cell, not an error, not a fabricated deadline (§2.11).
    expect(screen.getByText("No due time")).toBeDefined();
  });

  it("marks an overdue row in words, never a negative duration and never colour alone", async () => {
    listInbox.mockResolvedValue({ items: [OVERDUE], total: 1, page: 1, size: 25, pages: 1 });
    renderInbox();

    await waitFor(() => expect(screen.getByText("Kabir Shah")).toBeDefined());
    expect(screen.getByText("Overdue by 5 hours")).toBeDefined();
  });

  it("filters to overdue rows only — a row with no deadline is never overdue", async () => {
    listInbox.mockResolvedValue({
      items: [WITH_DUE, NO_DUE, OVERDUE],
      total: 3,
      page: 1,
      size: 25,
      pages: 1,
    });
    const user = userEvent.setup();
    renderInbox();

    await waitFor(() => expect(screen.getByText("Emily Watson")).toBeDefined());
    await user.click(screen.getByRole("button", { name: "Overdue" }));

    expect(screen.getByText("Kabir Shah")).toBeDefined();
    expect(screen.queryByText("Emily Watson")).toBeNull();
    expect(screen.queryByText("Ananya Iyer")).toBeNull();

    await user.click(screen.getByRole("button", { name: "All" }));
    expect(screen.getByText("Emily Watson")).toBeDefined();
    expect(screen.getByText("Ananya Iyer")).toBeDefined();
  });

  it("shows the successor step's deadline on an escalated row, not the closed predecessor's", async () => {
    // The server sends one `due_at` per row: the step in front of the reader now. The predecessor
    // escalated two hours ago and its deadline is behind us — if the row rendered that, it would
    // read "Overdue by 2 hours" instead of the successor's "Due in 4 hours".
    const escalated: InboxItemOut = {
      ...ITEM,
      id: "req-escalated",
      requester_name: "Rohan Mehta",
      inbox_reason: "escalated_to_me",
      from_name: "Priya Nair",
      due_at: hoursFromNow(4.5),
    };
    listInbox.mockResolvedValue({ items: [escalated], total: 1, page: 1, size: 25, pages: 1 });
    renderInbox();

    await waitFor(() => expect(screen.getByText("Rohan Mehta")).toBeDefined());
    expect(screen.getByText("Due in 4 hours")).toBeDefined();
    // Scoped to the table: "Overdue" is also the name of a filter pill, which is always present.
    expect(within(screen.getByRole("table")).queryByText(/Overdue/)).toBeNull();
  });

  it("has a fixture for each Due rendering, so the demo exercises all three", async () => {
    // Fixture mode is how this app is demonstrated, and a state with no fixture is a state nobody
    // looks at. Asserted on the data rather than through the screen because `USE_FIXTURES` is an
    // env switch the normal test run deliberately leaves off.
    const { FIXTURE_INBOX } = await import("@/api/fixtures");
    const now = Date.now();
    const due = FIXTURE_INBOX.map((i) => i.due_at);
    expect(due.some((d) => d === null)).toBe(true);
    expect(due.some((d) => d !== null && new Date(d).getTime() > now)).toBe(true);
    expect(due.some((d) => d !== null && new Date(d).getTime() < now)).toBe(true);
  });

  it("keeps an overdue row operable — the deadline styles the row, it does not gate it", async () => {
    listInbox.mockResolvedValue({ items: [OVERDUE], total: 1, page: 1, size: 25, pages: 1 });
    const user = userEvent.setup();
    renderInbox();

    await waitFor(() => expect(screen.getByText("Kabir Shah")).toBeDefined());
    // The overdue words are part of the open control's accessible name, not a tooltip-only affordance.
    const row = screen.getByRole("button", { name: /Kabir Shah.*Overdue by 5 hours/ });
    row.focus();
    await user.keyboard("{Enter}");
    await waitFor(() => expect(screen.getByText("Request detail")).toBeDefined());
  });
});
