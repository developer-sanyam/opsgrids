/**
 * `fetchMyNotifications`/`markNotificationRead`/`markAllNotificationsRead` are stubbed here,
 * deliberately: what this suite protects is the hook's own bookkeeping — pagination, the
 * optimistic list and badge updates, and that a failed write surfaces without corrupting the
 * list — not the HTTP layer, which `http.test.ts` already covers.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import * as api from "./api";
import type { NotificationItem, NotificationPage } from "./api";
import { useNotifications } from "./useNotifications";

vi.mock("./api", () => ({
  fetchMyNotifications: vi.fn(),
  markNotificationRead: vi.fn(),
  markAllNotificationsRead: vi.fn(),
}));

function item(overrides: Partial<NotificationItem> = {}): NotificationItem {
  return {
    id: "n-1",
    category: "operational",
    subject: null,
    body: "Something happened",
    event_type: "support.desk.ticket.assigned",
    rule_code: "notify_assignee",
    created_at: "2026-09-25T09:00:00Z",
    read_at: null,
    ...overrides,
  };
}

function page(overrides: Partial<NotificationPage> = {}): NotificationPage {
  return { items: [item()], next_cursor: null, unread_count: 1, ...overrides };
}

function Harness({ unreadOnly = false }: { unreadOnly?: boolean }) {
  const { items, unreadCount, loading, loadingMore, hasMore, error, refresh, loadMore, markRead, markAllRead } =
    useNotifications({ unreadOnly });
  return (
    <>
      <p>loading: {loading ? "yes" : "no"}</p>
      <p>loadingMore: {loadingMore ? "yes" : "no"}</p>
      <p>unread: {unreadCount}</p>
      <p>hasMore: {hasMore ? "yes" : "no"}</p>
      {error ? <p>error: {error.message}</p> : null}
      <ul>
        {items.map((n) => (
          <li key={n.id}>
            {n.body} · {n.read_at ? "read" : "unread"}
          </li>
        ))}
      </ul>
      <button type="button" onClick={refresh}>
        Refresh
      </button>
      <button type="button" onClick={loadMore}>
        Load more
      </button>
      <button type="button" onClick={() => void markRead("n-1")}>
        Mark n-1 read
      </button>
      <button type="button" onClick={() => void markAllRead()}>
        Mark all read
      </button>
    </>
  );
}

describe("useNotifications", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("loads the first page on mount and reports the unread count", async () => {
    vi.mocked(api.fetchMyNotifications).mockResolvedValue(page());
    render(<Harness />);

    expect(screen.getByText("loading: yes")).toBeTruthy();
    await screen.findByText("Something happened · unread");
    expect(screen.getByText("loading: no")).toBeTruthy();
    expect(screen.getByText("unread: 1")).toBeTruthy();
    expect(api.fetchMyNotifications).toHaveBeenCalledWith({ limit: undefined, unreadOnly: false });
  });

  it("surfaces a failed load without throwing", async () => {
    vi.mocked(api.fetchMyNotifications).mockRejectedValue(new Error("network down"));
    render(<Harness />);

    await screen.findByText("error: network down");
    expect(screen.getByText("loading: no")).toBeTruthy();
  });

  it("loadMore appends the next page and stops once next_cursor is null", async () => {
    const user = userEvent.setup();
    vi.mocked(api.fetchMyNotifications).mockResolvedValueOnce(
      page({ items: [item({ id: "n-1", body: "First" })], next_cursor: "cursor-1", unread_count: 2 }),
    );
    render(<Harness />);
    await screen.findByText("First · unread");
    expect(screen.getByText("hasMore: yes")).toBeTruthy();

    vi.mocked(api.fetchMyNotifications).mockResolvedValueOnce(
      page({ items: [item({ id: "n-2", body: "Second" })], next_cursor: null, unread_count: 2 }),
    );
    await user.click(screen.getByRole("button", { name: "Load more" }));

    await screen.findByText("Second · unread");
    expect(screen.getByText("First · unread")).toBeTruthy();
    expect(screen.getByText("hasMore: no")).toBeTruthy();
    expect(api.fetchMyNotifications).toHaveBeenLastCalledWith({
      limit: undefined,
      unreadOnly: false,
      cursor: "cursor-1",
    });
  });

  it("markRead updates the item and decrements the unread count optimistically", async () => {
    const user = userEvent.setup();
    vi.mocked(api.fetchMyNotifications).mockResolvedValue(page());
    vi.mocked(api.markNotificationRead).mockResolvedValue(
      item({ read_at: "2026-09-25T09:05:00Z" }),
    );
    render(<Harness />);
    await screen.findByText("Something happened · unread");

    await user.click(screen.getByRole("button", { name: "Mark n-1 read" }));

    await screen.findByText("Something happened · read");
    expect(screen.getByText("unread: 0")).toBeTruthy();
    expect(api.markNotificationRead).toHaveBeenCalledWith("n-1");
  });

  it("markRead removes the item entirely when the feed is unread-only", async () => {
    const user = userEvent.setup();
    vi.mocked(api.fetchMyNotifications).mockResolvedValue(page());
    vi.mocked(api.markNotificationRead).mockResolvedValue(
      item({ read_at: "2026-09-25T09:05:00Z" }),
    );
    render(<Harness unreadOnly />);
    await screen.findByText("Something happened · unread");

    await user.click(screen.getByRole("button", { name: "Mark n-1 read" }));

    await waitFor(() => {
      expect(screen.queryByText(/Something happened/)).toBeNull();
    });
    expect(screen.getByText("unread: 0")).toBeTruthy();
  });

  it("markAllRead clears every unread item and reports the failure", async () => {
    const user = userEvent.setup();
    vi.mocked(api.fetchMyNotifications).mockResolvedValue(
      page({
        items: [item({ id: "n-1", body: "One" }), item({ id: "n-2", body: "Two" })],
        unread_count: 2,
      }),
    );
    vi.mocked(api.markAllNotificationsRead).mockRejectedValue(new Error("gateway timeout"));
    render(<Harness />);
    await screen.findByText("One · unread");

    await user.click(screen.getByRole("button", { name: "Mark all read" }));

    await screen.findByText("One · read");
    expect(screen.getByText("Two · read")).toBeTruthy();
    expect(screen.getByText("unread: 0")).toBeTruthy();
    await screen.findByText("error: gateway timeout");
  });
});
