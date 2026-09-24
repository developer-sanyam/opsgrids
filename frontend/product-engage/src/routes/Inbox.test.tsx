import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

import { ApiError, PermissionProvider } from "@opsgrids/client";
import type { ProblemJson } from "@opsgrids/client";

import * as client from "@/api/client";
import type {
  ConversationMessageOut,
  ConversationOut,
  ConversationReplyOut,
  Page,
  ProviderTemplateOut,
} from "@/api/types";
import { InboxPage } from "./Inbox";

/**
 * The inbox, from the two sides that matter most: **a caller without `message.reply` cannot see
 * a way to send**, and **a conversation outside its service window never offers a free-text box**
 * — WhatsApp itself refuses that send, and a composer that looked like it would work would be a
 * lie the customer never sees but the agent does.
 */

function page<T>(items: T[]): Page<T> {
  return { items, total: items.length, page: 1, size: 25, pages: items.length ? 1 : 0 };
}

const OPEN: ConversationOut = {
  id: "b1a2c3d4-1111-4a1a-8a1a-000000000001",
  channel: "whatsapp",
  wa_address: "•••••• 4210",
  contact_id: "c1a0f2e4-9b3d-4a7c-8e1f-2d6b9c4a5e10",
  last_customer_message_at: new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString(),
  within_service_window: true,
  status: "open",
  assignee_membership_id: null,
  created_at: "2026-09-20T09:00:00Z",
  updated_at: "2026-09-23T10:00:00Z",
};

const EXPIRED: ConversationOut = {
  ...OPEN,
  id: "b1a2c3d4-3333-4a1a-8a1a-000000000003",
  wa_address: "•••••• 5599",
  contact_id: null,
  within_service_window: false,
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

const TEMPLATE: ProviderTemplateOut = {
  id: "delivery_confirmed",
  channel: "whatsapp",
  provider_name: "meta",
  provider_template_id: "1",
  language: "en",
  category: "utility",
  parameter_format: "positional",
  status: "approved",
  status_reason: null,
  status_checked_at: "2026-09-21T04:12:09Z",
  variables: [],
  preview_text: "Hi, your shipment is out for delivery.",
  sendable: true,
  created_at: "2026-08-30T09:14:22Z",
  updated_at: "2026-09-21T04:12:09Z",
};

const ALL_GRANTS = [
  "engage.inbox.conversation.read:ORG",
  "engage.inbox.conversation.write:ORG",
  "engage.inbox.message.reply:ORG",
];

function renderInbox(grants: string[] = ALL_GRANTS) {
  return render(
    <PermissionProvider grants={grants}>
      <MemoryRouter>
        <InboxPage />
      </MemoryRouter>
    </PermissionProvider>,
  );
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("loading and empty states", () => {
  it("shows an empty state with no conversations", async () => {
    vi.spyOn(client, "listConversations").mockResolvedValue(page([]));
    renderInbox();
    expect(await screen.findByText("No conversations yet")).toBeTruthy();
  });

  it("shows the 402 not-active state, not an error, when Engage isn't on", async () => {
    vi.spyOn(client, "listConversations").mockRejectedValue(
      new ApiError(402, "engage is not active for this organization", {
        type: "product-not-entitled",
        status: 402,
      }),
    );
    renderInbox();
    expect(await screen.findByText("Engage isn't active for this organization yet")).toBeTruthy();
  });
});

describe("the list and the thread", () => {
  it("selecting a conversation loads its thread and shows the CRM link when matched", async () => {
    vi.spyOn(client, "listConversations").mockResolvedValue(page([OPEN]));
    vi.spyOn(client, "listConversationMessages").mockResolvedValue(page(THREAD));
    renderInbox();

    const row = await screen.findByRole("button", { name: /4210/ });
    await userEvent.click(row);

    expect(await screen.findByText("Is my order ready?")).toBeTruthy();
    expect(screen.getByRole("link", { name: /view in Revenue/i })).toBeTruthy();
  });

  it("shows no CRM link for a stranger number", async () => {
    vi.spyOn(client, "listConversations").mockResolvedValue(page([EXPIRED]));
    vi.spyOn(client, "listConversationMessages").mockResolvedValue(page(THREAD));
    vi.spyOn(client, "listWhatsAppTemplates").mockResolvedValue(page([TEMPLATE]));
    renderInbox();

    await userEvent.click(await screen.findByRole("button", { name: /5599/ }));
    await screen.findByText("Is my order ready?");
    expect(screen.queryByRole("link", { name: /view in Revenue/i })).toBeNull();
    expect(screen.getByText("No CRM contact matched this number yet.")).toBeTruthy();
  });
});

describe("the composer", () => {
  it("sends a reply inside the window and clears the box", async () => {
    vi.spyOn(client, "listConversations").mockResolvedValue(page([OPEN]));
    vi.spyOn(client, "listConversationMessages").mockResolvedValue(page(THREAD));
    const reply = vi.spyOn(client, "replyToConversation").mockResolvedValue({
      message_id: "msg-1",
      state: "pending",
      conversation_id: OPEN.id,
    } satisfies ConversationReplyOut);
    renderInbox();

    await userEvent.click(await screen.findByRole("button", { name: /4210/ }));
    const box = await screen.findByPlaceholderText("Type a reply…");
    await userEvent.type(box, "On it now");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() => expect(reply).toHaveBeenCalledTimes(1));
    expect(reply.mock.calls[0]?.[1]?.body).toBe("On it now");
    await waitFor(() => expect((box as HTMLTextAreaElement).value).toBe(""));
  });

  it("shows the template picker instead of a composer once the window has closed", async () => {
    vi.spyOn(client, "listConversations").mockResolvedValue(page([EXPIRED]));
    vi.spyOn(client, "listConversationMessages").mockResolvedValue(page(THREAD));
    vi.spyOn(client, "listWhatsAppTemplates").mockResolvedValue(page([TEMPLATE]));
    renderInbox();

    await userEvent.click(await screen.findByRole("button", { name: /5599/ }));
    expect(
      await screen.findByText(/24-hour window has closed/),
    ).toBeTruthy();
    expect(screen.getByText(TEMPLATE.provider_name)).toBeTruthy();
    expect(screen.queryByPlaceholderText("Type a reply…")).toBeNull();
  });

  it("switches to the template picker when a send comes back refused for the window", async () => {
    vi.spyOn(client, "listConversations").mockResolvedValue(page([OPEN]));
    vi.spyOn(client, "listConversationMessages").mockResolvedValue(page(THREAD));
    vi.spyOn(client, "listWhatsAppTemplates").mockResolvedValue(page([TEMPLATE]));
    vi.spyOn(client, "replyToConversation").mockRejectedValue(
      new ApiError(
        409,
        "This conversation's 24-hour service window has closed.",
        { status: 409, remedy: "template" } as unknown as ProblemJson,
      ),
    );
    renderInbox();

    await userEvent.click(await screen.findByRole("button", { name: /4210/ }));
    const box = await screen.findByPlaceholderText("Type a reply…");
    await userEvent.type(box, "too late");
    await userEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByText(/24-hour window has closed/)).toBeTruthy();
  });
});

describe("permission gating", () => {
  it("hides the composer and the Close action for a read-only caller", async () => {
    vi.spyOn(client, "listConversations").mockResolvedValue(page([OPEN]));
    vi.spyOn(client, "listConversationMessages").mockResolvedValue(page(THREAD));
    renderInbox(["engage.inbox.conversation.read:ORG"]);

    await userEvent.click(await screen.findByRole("button", { name: /4210/ }));
    await screen.findByText("Is my order ready?");

    expect(screen.queryByPlaceholderText("Type a reply…")).toBeNull();
    expect(screen.getByText(/You don.t have permission to reply in this inbox\./)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Close" })).toBeNull();
  });
});

describe("when a refresh is refused mid-poll", () => {
  it("keeps the conversations that were on screen and says they are no longer kept current", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      vi.spyOn(client, "listConversations")
        .mockResolvedValueOnce(page([OPEN]))
        .mockRejectedValue(new ApiError(403, "You do not have engage.inbox.conversation.read", null));
      renderInbox();

      expect(await screen.findByRole("button", { name: /4210/ })).toBeTruthy();
      await vi.advanceTimersByTimeAsync(16_000);

      expect(await screen.findByText("Live updates stopped")).toBeTruthy();
      // Not replaced by an error: what was true a moment ago is still there.
      expect(screen.getByRole("button", { name: /4210/ })).toBeTruthy();
      expect(screen.queryByText("Couldn't load your conversations")).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });
});
