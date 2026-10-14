/**
 * The queue and the ticket page on a phone: filters live in the shared FilterBar (a sheet below
 * 768px), the ticket's status / SLA / assignee come first and fold away, and replying is one tap
 * from anywhere in a long thread with a validation message in place of a dead button.
 */
import { AuthContext, PermissionProvider } from "@opsgrids/client";
import type { AuthContextValue } from "@opsgrids/client";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as client from "@/api/client";
import type { Agent, Ticket } from "@/api/types";

import { QueuePage } from "./Queue";
import { TicketDetailPage } from "./TicketDetail";

function stubViewport(width: number) {
  vi.stubGlobal("matchMedia", (query: string) => {
    const min = /min-width:\s*(\d+)px/.exec(query);
    return {
      matches: min?.[1] !== undefined ? width >= Number(min[1]) : false,
      media: query,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
    };
  });
}

const AGENT: Agent = {
  id: "a-1",
  name: "Priya Nair",
  email: "priya@example.com",
  role: "agent",
  team: "Tier 1",
  open_load: 3,
  avg_first_response_min: 12,
  csat_pct: 96,
  solved_7d: 14,
  online: true,
};

function ticket(over: Partial<Ticket> = {}): Ticket {
  return {
    id: "TK-1001",
    subject: "Checkout is broken",
    requester: { name: "Helena Vogel", email: "helena@example.com", company: "Northwind", kind: "customer" },
    priority: "high",
    status: "open",
    channel: "email",
    assignee_id: "a-1",
    tags: [],
    created_at: "2026-06-01T09:00:00Z",
    updated_at: "2026-06-01T10:00:00Z",
    first_response_at: null,
    sla_due: "2026-06-02T09:00:00Z",
    sla_breached: false,
    satisfaction: null,
    messages: [
      { id: "m-1", author_type: "customer", author_name: "Helena Vogel", at: "2026-06-01T09:00:00Z", body: "It is on fire." },
    ],
    ...over,
  };
}

const AUTH: AuthContextValue = {
  status: "authenticated",
  user: {
    user_id: "u-1",
    email: "agent@example.com",
    full_name: "Asha Rao",
    organization_id: "org-1",
    membership_id: "m-1",
    roles: [],
    amr: [],
    must_change_password: false,
    mfa_enabled: true,
    organizations: [],
  },
  login: vi.fn(),
  verifyMfa: vi.fn(),
  selectOrg: vi.fn(),
  logout: vi.fn(),
};

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function renderQueue() {
  const list = vi
    .spyOn(client, "listTickets")
    .mockResolvedValue({ items: [ticket()], total: 1, page: 1, size: 25, pages: 1 });
  vi.spyOn(client, "listAgents").mockResolvedValue([AGENT]);
  render(
    <MemoryRouter>
      <AuthContext.Provider value={AUTH}>
        <PermissionProvider grants={["support.desk.ticket.write:ORG"]}>
          <QueuePage />
        </PermissionProvider>
      </AuthContext.Provider>
    </MemoryRouter>,
  );
  return list;
}

describe("Queue filters", () => {
  it("are the shared FilterBar: labelled selects in the row from 768px, filtering the server query", async () => {
    stubViewport(1280);
    const list = renderQueue();
    await screen.findByText("Checkout is broken");

    await userEvent.selectOptions(screen.getByLabelText("Status"), "open");

    await waitFor(() => expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ status: "open" })));
    expect(screen.getByRole("list", { name: "Active filters" })).toBeTruthy();
  });

  it("collapse into a Filters sheet on a phone and apply from it", async () => {
    stubViewport(390);
    const list = renderQueue();
    await screen.findByText("Checkout is broken");

    expect(screen.queryByLabelText("Status")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Filters" }));
    const sheet = await screen.findByRole("dialog", { name: "Filters" });
    await userEvent.selectOptions(within(sheet).getByLabelText("Priority"), "urgent");

    await waitFor(() => expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ priority: "urgent" })));
    expect(within(sheet).getByRole("button", { name: "Show results" })).toBeTruthy();
  });
});

function renderDetail() {
  vi.spyOn(client, "getTicket").mockResolvedValue(ticket());
  vi.spyOn(client, "listAgents").mockResolvedValue([AGENT]);
  const reply = vi.spyOn(client, "replyToTicket").mockResolvedValue(ticket());
  render(
    <MemoryRouter initialEntries={["/queue/TK-1001"]}>
      <AuthContext.Provider value={AUTH}>
        <PermissionProvider grants={["support.desk.ticket.write:ORG"]}>
          <Routes>
            <Route path="/queue/:id" element={<TicketDetailPage />} />
          </Routes>
        </PermissionProvider>
      </AuthContext.Provider>
    </MemoryRouter>,
  );
  return reply;
}

describe("Ticket detail on a phone", () => {
  it("puts status, SLA and assignee ahead of the conversation, folded away", async () => {
    stubViewport(390);
    renderDetail();
    const summary = await screen.findByText("Ticket details");
    const details = summary.closest("details");
    expect(details).not.toBeNull();
    expect(details?.open).toBe(false);
    expect(within(summary.closest("summary") as HTMLElement).getByText("Priya Nair")).toBeTruthy();

    const conversation = screen.getByRole("heading", { name: "Conversation" });
    expect(details?.compareDocumentPosition(conversation)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
  });

  it("offers a Reply action that focuses the reply box", async () => {
    stubViewport(390);
    renderDetail();
    // jsdom has no layout, so scrolling is a no-op to stub.
    Element.prototype.scrollIntoView = vi.fn();
    await userEvent.click(await screen.findByRole("button", { name: "Reply" }));
    expect(document.activeElement).toBe(screen.getByLabelText("Reply", { selector: "textarea" }));
  });

  it("keeps Send reply enabled and explains an empty reply instead", async () => {
    stubViewport(390);
    const reply = renderDetail();
    const send = await screen.findByRole("button", { name: "Send reply" });
    expect((send as HTMLButtonElement).disabled).toBe(false);

    await userEvent.click(send);

    expect(screen.getByText("Write something before sending.")).toBeTruthy();
    expect(reply).not.toHaveBeenCalled();
  });
});
