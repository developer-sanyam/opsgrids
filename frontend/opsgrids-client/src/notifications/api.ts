/**
 * The caller's own in-app notification feed — `notification` service's `GET /notifications/me`
 * and its two mark-read routes. Scoped by identity server-side, the same way
 * `GET /members/me/grants` is: there is no `?membership_id=`, so this can never become a way to
 * read someone else's notifications.
 */

import { apiRequest } from "../http";

export interface NotificationItem {
  id: string;
  category: string;
  subject: string | null;
  body: string;
  event_type: string | null;
  rule_code: string | null;
  created_at: string;
  read_at: string | null;
}

export interface NotificationPage {
  items: NotificationItem[];
  /** Pass back as `cursor` to fetch the next page. `null` on the last one. */
  next_cursor: string | null;
  /** Folded server-side from the same filter as the page, minus the cursor and `unread_only` —
   *  a bell badge never needs a request of its own. */
  unread_count: number;
}

export interface FetchNotificationsParams {
  /** The `next_cursor` from a previous page. Omit for the first page. */
  cursor?: string | undefined;
  /** Defaults to the service's own default (25) when omitted. */
  limit?: number | undefined;
  /** Only unread notifications. Default false — the full feed. */
  unreadOnly?: boolean | undefined;
}

function toQueryString(params: FetchNotificationsParams): string {
  const search = new URLSearchParams();
  if (params.cursor) search.set("cursor", params.cursor);
  if (params.limit !== undefined) search.set("limit", String(params.limit));
  if (params.unreadOnly) search.set("unread_only", "true");
  const qs = search.toString();
  return qs ? `?${qs}` : "";
}

export function fetchMyNotifications(
  params: FetchNotificationsParams = {},
): Promise<NotificationPage> {
  return apiRequest<NotificationPage>(`/notifications/me${toQueryString(params)}`);
}

export function markNotificationRead(id: string): Promise<NotificationItem> {
  return apiRequest<NotificationItem>(`/notifications/me/${encodeURIComponent(id)}:read`, {
    method: "POST",
  });
}

export interface MarkAllReadResult {
  updated: number;
}

export function markAllNotificationsRead(): Promise<MarkAllReadResult> {
  return apiRequest<MarkAllReadResult>("/notifications/me:read-all", { method: "POST" });
}
