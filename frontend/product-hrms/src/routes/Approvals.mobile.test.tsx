/**
 * §4.A on a phone: each request is a card, and Approve and Reject are on the card. They open the
 * request with the decision already chosen, where the same confirmation and server checks apply.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";

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

function stubPhone(): void {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    addListener: () => undefined,
    removeListener: () => undefined,
    dispatchEvent: () => false,
  }));
}

function Detail() {
  const location = useLocation();
  const decide = (location.state as { decide?: string } | null)?.decide ?? "none";
  return <div>Request detail, deciding: {decide}</div>;
}

function renderInbox() {
  return render(
    <MemoryRouter initialEntries={["/approvals"]}>
      <Routes>
        <Route path="/approvals" element={<ApprovalsPage />} />
        <Route path="/approvals/requests/:requestId" element={<Detail />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("Approvals inbox on a phone", () => {
  beforeEach(() => {
    listInbox.mockReset();
    stubPhone();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("puts Approve and Reject on the card and opens the request with that decision chosen", async () => {
    listInbox.mockResolvedValue({ items: [ITEM], total: 1, page: 1, size: 25, pages: 1 });
    const user = userEvent.setup();
    renderInbox();

    await waitFor(() => expect(screen.getByText("Ananya Iyer")).toBeDefined());
    expect(screen.queryByRole("table")).toBeNull();

    await user.click(screen.getByRole("button", { name: "Reject request from Ananya Iyer" }));
    await waitFor(() => expect(screen.getByText("Request detail, deciding: reject")).toBeDefined());
  });

  it("keeps the whole card as the way to open the request without deciding", async () => {
    listInbox.mockResolvedValue({ items: [ITEM], total: 1, page: 1, size: 25, pages: 1 });
    const user = userEvent.setup();
    renderInbox();

    await waitFor(() => expect(screen.getByText("Ananya Iyer")).toBeDefined());
    // The card's own open control comes first in the document; Approve and Reject follow it.
    const [openCard] = screen.getAllByRole("button", { name: /Ananya Iyer/ });
    await user.click(openCard as HTMLElement);
    await waitFor(() => expect(screen.getByText("Request detail, deciding: none")).toBeDefined());
  });
});

describe("Approvals inbox on a wide screen", () => {
  it("has no Approve or Reject on the rows: the request's own page is where a decision is made", async () => {
    listInbox.mockReset();
    listInbox.mockResolvedValue({ items: [ITEM], total: 1, page: 1, size: 25, pages: 1 });
    renderInbox();
    await waitFor(() => expect(screen.getByText("Ananya Iyer")).toBeDefined());
    expect(screen.queryByRole("button", { name: /Approve request from/ })).toBeNull();
  });
});
