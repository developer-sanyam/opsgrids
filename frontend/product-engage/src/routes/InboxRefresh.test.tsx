import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

import { ApiError, PermissionProvider } from "@opsgrids/client";

import * as client from "@/api/client";
import type { ConversationMessageOut, ConversationOut, Page } from "@/api/types";
import { InboxPage } from "./Inbox";

/**
 * What the inbox does when the list is re-read *because of something the person just did* — the
 * thread's `onChanged` is the poll's `refresh`. A refresh is not a change of what is being read, so
 * it must not blank the list to a skeleton, and it must not throw away a list that a stopped poll
 * had kept.
 */

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

const GRANTS = [
  "engage.inbox.conversation.read:ORG",
  "engage.inbox.conversation.write:ORG",
  "engage.inbox.message.reply:ORG",
];

function page<T>(items: T[]): Page<T> {
  return { items, total: items.length, page: 1, size: 25, pages: items.length ? 1 : 0 };
}

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

function renderInbox() {
  return render(
    <PermissionProvider grants={GRANTS}>
      <MemoryRouter>
        <InboxPage />
      </MemoryRouter>
    </PermissionProvider>,
  );
}

function mockThread() {
  vi.spyOn(client, "listConversationMessages").mockResolvedValue(page(THREAD));
  vi.spyOn(client, "closeConversation").mockResolvedValue({ ...OPEN, status: "closed" });
}

describe("re-reading the list after an action in the thread", () => {
  it("keeps the conversations on screen while it is re-read, rather than blanking them to a skeleton", async () => {
    mockThread();
    let release: (value: Page<ConversationOut>) => void = () => {};
    vi.spyOn(client, "listConversations")
      .mockResolvedValueOnce(page([OPEN]))
      .mockReturnValueOnce(
        new Promise<Page<ConversationOut>>((resolve) => {
          release = resolve;
        }),
      );
    const user = userEvent.setup();
    const { container } = renderInbox();

    await user.click(await screen.findByRole("button", { name: /4210/ }));
    await user.click(await screen.findByRole("button", { name: "Close" }));

    // The re-read is in flight: the row is still there and no skeleton has replaced it.
    await waitFor(() => expect(client.listConversations).toHaveBeenCalledTimes(2));
    expect(screen.getByRole("button", { name: /4210/ })).toBeTruthy();
    expect(container.querySelectorAll('[class*="animate-pulse"]').length).toBe(0);

    release(page([{ ...OPEN, status: "closed" }]));
    await waitFor(() => expect(screen.getByRole("button", { name: /4210/ })).toBeTruthy());
  });

  it("keeps the conversations a stopped poll had kept, and still says updates stopped", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockThread();
    const refused = new ApiError(403, "You do not have engage.inbox.conversation.read", null);
    vi.spyOn(client, "listConversations").mockResolvedValueOnce(page([OPEN])).mockRejectedValue(refused);
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    renderInbox();

    await user.click(await screen.findByRole("button", { name: /4210/ }));
    await vi.advanceTimersByTimeAsync(16_000);
    expect(await screen.findByText("Live updates stopped")).toBeTruthy();

    // The person acts; the thread asks the list to refresh; the service refuses again.
    await user.click(await screen.findByRole("button", { name: "Close" }));

    await waitFor(() => expect(client.listConversations).toHaveBeenCalledTimes(3));
    // Not blanked, not replaced by an error: what was kept is still there, and still marked stale.
    expect(screen.getByRole("button", { name: /4210/ })).toBeTruthy();
    expect(screen.queryByText("Couldn't load your conversations")).toBeNull();
    expect(screen.getByText("Live updates stopped")).toBeTruthy();
  });

  it("resumes when the re-read succeeds, and stops saying updates stopped", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockThread();
    const refused = new ApiError(403, "no", null);
    vi.spyOn(client, "listConversations")
      .mockResolvedValueOnce(page([OPEN]))
      .mockRejectedValueOnce(refused)
      .mockResolvedValue(page([OPEN]));
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    renderInbox();

    await user.click(await screen.findByRole("button", { name: /4210/ }));
    await vi.advanceTimersByTimeAsync(16_000);
    await screen.findByText("Live updates stopped");

    await user.click(await screen.findByRole("button", { name: "Close" }));

    await waitFor(() => expect(screen.queryByText("Live updates stopped")).toBeNull());
    expect(screen.getByRole("button", { name: /4210/ })).toBeTruthy();
  });
});
