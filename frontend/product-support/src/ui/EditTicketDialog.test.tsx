import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { Ticket } from "@/api/types";
import { EditTicketDialog } from "./EditTicketDialog";

const TICKET: Ticket = {
  id: "TK-4001",
  subject: "Checkout returns 500",
  requester: { name: "Helena Vogel", email: "helena@example.com", company: "Northwind", kind: "customer" },
  priority: "urgent",
  status: "open",
  channel: "email",
  assignee_id: null,
  tags: ["payments", "outage"],
  created_at: "2026-08-24T11:02:00Z",
  updated_at: "2026-08-24T13:40:00Z",
  first_response_at: null,
  sla_due: null,
  sla_breached: false,
  satisfaction: null,
  messages: [],
};

function jsonResponse(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function stubFetch(handler: (init: RequestInit) => Response) {
  vi.stubGlobal("fetch", vi.fn(async (_input: RequestInfo | URL, init: RequestInit = {}) => handler(init)));
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("EditTicketDialog", () => {
  it("pre-fills every field from the ticket being edited", () => {
    stubFetch(() => jsonResponse(200, TICKET));
    render(<EditTicketDialog ticket={TICKET} onClose={vi.fn()} onSaved={vi.fn()} />);
    expect(screen.getByLabelText("Subject")).toHaveProperty("value", TICKET.subject);
    expect(screen.getByLabelText(/^Tags/)).toHaveProperty("value", "payments, outage");
  });

  it("blocks submit on an empty subject with our own message, not a round trip", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<EditTicketDialog ticket={TICKET} onClose={vi.fn()} onSaved={vi.fn()} />);

    await user.clear(screen.getByLabelText("Subject"));
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    expect(await screen.findByText("Give the ticket a subject, up to 300 characters.")).toBeTruthy();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("sends the PATCH with exactly priority, subject and the parsed tags", async () => {
    let sentBody: unknown = null;
    stubFetch((init) => {
      sentBody = JSON.parse(init.body as string);
      return jsonResponse(200, { ...TICKET, subject: "Checkout returns 500 for EU" });
    });
    const user = userEvent.setup();
    const onSaved = vi.fn();
    render(<EditTicketDialog ticket={TICKET} onClose={vi.fn()} onSaved={onSaved} />);

    await user.type(screen.getByLabelText("Subject"), " for EU");
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1));
    expect(sentBody).toEqual({
      subject: "Checkout returns 500 for EU",
      priority: "urgent",
      tags: ["payments", "outage"],
    });
  });

  it("maps a 422 onto the field it names, and keeps the entered value", async () => {
    stubFetch(() =>
      jsonResponse(422, {
        title: "Unprocessable Entity",
        status: 422,
        detail: "Validation failed",
        errors: [{ field: "subject", code: "string_too_long", message: "Keep the subject under 300 characters." }],
      }),
    );
    const user = userEvent.setup();
    render(<EditTicketDialog ticket={TICKET} onClose={vi.fn()} onSaved={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: "Save changes" }));

    expect(await screen.findByText("Keep the subject under 300 characters.")).toBeTruthy();
    expect(screen.getByLabelText("Subject")).toHaveProperty("value", TICKET.subject);
  });
});
