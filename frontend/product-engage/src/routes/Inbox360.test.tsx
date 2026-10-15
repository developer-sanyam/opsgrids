import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

import { PermissionProvider } from "@opsgrids/client";

import * as customer360 from "@/api/customer360";
import { CUSTOMER_360, CUSTOMER_ID } from "@/api/customer360.fixtures";
import * as client from "@/api/client";
import type { ConversationMessageOut, ConversationOut, Page } from "@/api/types";
import { InboxPage } from "./Inbox";

/**
 * Customer 360 inside the WhatsApp inbox: the panel follows the selected conversation's
 * `contact_id`, and a conversation with no matched contact is told so rather than guessed at.
 */

function page<T>(items: T[]): Page<T> {
  return { items, total: items.length, page: 1, size: 25, pages: items.length ? 1 : 0 };
}

const MATCHED: ConversationOut = {
  id: "b1a2c3d4-1111-4a1a-8a1a-000000000001",
  channel: "whatsapp",
  wa_address: "•••••• 4210",
  contact_id: CUSTOMER_ID,
  last_customer_message_at: new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString(),
  within_service_window: true,
  status: "open",
  assignee_membership_id: null,
  created_at: "2026-09-20T09:00:00Z",
  updated_at: "2026-09-23T10:00:00Z",
};

const STRANGER: ConversationOut = {
  ...MATCHED,
  id: "b1a2c3d4-2222-4a1a-8a1a-000000000002",
  wa_address: "•••••• 7788",
  contact_id: null,
};

const THREAD: ConversationMessageOut[] = [
  {
    id: "m1",
    direction: "inbound",
    message_type: "text",
    body: "Is my order ready?",
    occurred_at: "2026-09-23T08:00:00Z",
    provider_message_id: "wamid.1",
    profile_name: "Priya Raghavan",
  },
];

function renderInbox() {
  return render(
    <PermissionProvider
      grants={[
        "engage.inbox.conversation.read:ORG",
        "engage.inbox.conversation.write:ORG",
        "engage.inbox.message.reply:ORG",
      ]}
    >
      <MemoryRouter>
        <InboxPage />
      </MemoryRouter>
    </PermissionProvider>,
  );
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("Customer 360 in the inbox", () => {
  it("shows no panel until a conversation is selected", async () => {
    vi.spyOn(client, "listConversations").mockResolvedValue(page([MATCHED]));
    const load = vi.spyOn(customer360, "getCustomer360").mockResolvedValue(CUSTOMER_360);
    renderInbox();

    await screen.findByRole("button", { name: /4210/ });
    expect(screen.queryByRole("complementary", { name: "Customer 360" })).toBeNull();
    expect(load).not.toHaveBeenCalled();
  });

  it("loads the linked customer for the selected conversation", async () => {
    vi.spyOn(client, "listConversations").mockResolvedValue(page([MATCHED]));
    vi.spyOn(client, "listConversationMessages").mockResolvedValue(page(THREAD));
    const load = vi.spyOn(customer360, "getCustomer360").mockResolvedValue(CUSTOMER_360);
    renderInbox();

    await userEvent.click(await screen.findByRole("button", { name: /4210/ }));

    const panel = await screen.findByRole("complementary", { name: "Customer 360" });
    expect(await screen.findByText("Vaayu Foods · Bengaluru")).toBeTruthy();
    expect(panel).toBeTruthy();
    expect(load).toHaveBeenCalledWith(CUSTOMER_ID);
    // The thread is still there beside it.
    expect(screen.getByText("Is my order ready?")).toBeTruthy();
  });

  it("says no customer is linked for a number no contact was matched to, and does not ask", async () => {
    vi.spyOn(client, "listConversations").mockResolvedValue(page([STRANGER]));
    vi.spyOn(client, "listConversationMessages").mockResolvedValue(page(THREAD));
    const load = vi.spyOn(customer360, "getCustomer360").mockResolvedValue(CUSTOMER_360);
    renderInbox();

    await userEvent.click(await screen.findByRole("button", { name: /7788/ }));

    expect(await screen.findByText("No customer linked to this conversation")).toBeTruthy();
    expect(load).not.toHaveBeenCalled();
  });

  it("follows the selection from a matched conversation to a stranger", async () => {
    vi.spyOn(client, "listConversations").mockResolvedValue(page([MATCHED, STRANGER]));
    vi.spyOn(client, "listConversationMessages").mockResolvedValue(page(THREAD));
    vi.spyOn(customer360, "getCustomer360").mockResolvedValue(CUSTOMER_360);
    renderInbox();

    await userEvent.click(await screen.findByRole("button", { name: /4210/ }));
    expect(await screen.findByText("Vaayu Foods · Bengaluru")).toBeTruthy();

    await userEvent.click(screen.getByRole("button", { name: /7788/ }));
    expect(await screen.findByText("No customer linked to this conversation")).toBeTruthy();
    expect(screen.queryByText("Vaayu Foods · Bengaluru")).toBeNull();
  });
});

describe("Matched-contact link", () => {
  it("opens that contact's own CRM page, not the contact list", async () => {
    vi.spyOn(client, "listConversations").mockResolvedValue(page([MATCHED]));
    vi.spyOn(client, "listConversationMessages").mockResolvedValue(page(THREAD));
    vi.spyOn(customer360, "getCustomer360").mockResolvedValue(CUSTOMER_360);
    renderInbox();

    await userEvent.click(await screen.findByRole("button", { name: /4210/ }));

    const link = await screen.findByRole("link", { name: /Matched to a CRM contact/ });
    expect(link.getAttribute("href")).toMatch(new RegExp(`/contacts/${CUSTOMER_ID}$`));
  });
});
