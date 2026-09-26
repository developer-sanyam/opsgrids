/**
 * The API calls are stubbed, as in `useNotifications.test.tsx`: what this protects is the bell's
 * own contract — nothing rendered while there is nothing to show or the feed failed, the unread
 * badge, and that opening a row marks it read.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import * as api from "./api";
import type { NotificationItem, NotificationPage } from "./api";
import { NotificationsBell } from "./NotificationsBell";

vi.mock("./api", () => ({
  fetchMyNotifications: vi.fn(),
  markNotificationRead: vi.fn(),
  markAllNotificationsRead: vi.fn(),
}));

function item(overrides: Partial<NotificationItem> = {}): NotificationItem {
  return {
    id: "n-1",
    category: "operational",
    subject: "Ticket assigned",
    body: "TCK-12 was assigned to you",
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

afterEach(() => {
  vi.resetAllMocks();
});

describe("NotificationsBell", () => {
  it("renders nothing when the feed is empty", async () => {
    vi.mocked(api.fetchMyNotifications).mockResolvedValue(page({ items: [], unread_count: 0 }));
    const { container } = render(<NotificationsBell />);

    await waitFor(() => expect(api.fetchMyNotifications).toHaveBeenCalled());
    expect(container.firstChild).toBeNull();
  });

  it("renders nothing when the feed fails to load", async () => {
    vi.mocked(api.fetchMyNotifications).mockRejectedValue(new Error("boom"));
    const { container } = render(<NotificationsBell />);

    await waitFor(() => expect(api.fetchMyNotifications).toHaveBeenCalled());
    await waitFor(() => expect(container.firstChild).toBeNull());
  });

  it("names the unread count on the trigger and shows it as a badge", async () => {
    vi.mocked(api.fetchMyNotifications).mockResolvedValue(
      page({ items: [item(), item({ id: "n-2" })], unread_count: 2 }),
    );
    render(<NotificationsBell />);

    expect(await screen.findByLabelText("Notifications, 2 unread")).toBeTruthy();
    expect(screen.getByText("2")).toBeTruthy();
  });

  it("marks a row read when it is opened", async () => {
    vi.mocked(api.fetchMyNotifications).mockResolvedValue(page());
    vi.mocked(api.markNotificationRead).mockResolvedValue(item({ read_at: "2026-09-25T10:00:00Z" }));
    const user = userEvent.setup();
    render(<NotificationsBell />);

    await user.click(await screen.findByLabelText("Notifications, 1 unread"));
    await user.click(screen.getByRole("button", { name: /Ticket assigned/ }));

    await waitFor(() => expect(api.markNotificationRead).toHaveBeenCalledWith("n-1"));
    await waitFor(() => expect(screen.queryByText("(unread)")).toBeNull());
    expect(screen.queryByLabelText(/unread/)).toBeNull();
  });

  it("marks everything read from the panel", async () => {
    vi.mocked(api.fetchMyNotifications).mockResolvedValue(
      page({ items: [item(), item({ id: "n-2" })], unread_count: 2 }),
    );
    vi.mocked(api.markAllNotificationsRead).mockResolvedValue({ updated: 2 });
    const user = userEvent.setup();
    render(<NotificationsBell />);

    await user.click(await screen.findByLabelText("Notifications, 2 unread"));
    await user.click(screen.getByRole("button", { name: "Mark all read" }));

    await waitFor(() => expect(api.markAllNotificationsRead).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Mark all read" })).toBeNull());
    expect(screen.queryByLabelText(/unread/)).toBeNull();
    expect(screen.queryByText("(unread)")).toBeNull();
  });
});
