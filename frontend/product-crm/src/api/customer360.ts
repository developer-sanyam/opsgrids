/**
 * `GET /customers/{contact_id}/360` — the messaging half of the customer 360 (`channels`,
 * `timeline`). Same gateway prefix as every CRM read; the service answers 200 with a section
 * `null` and named in `unavailable` when it could not produce it.
 */

import { apiRequest } from "@opsgrids/client";

export interface MessagingChannels {
  whatsapp: {
    opted_in: boolean;
    window_open_until: string | null;
    last_message_at: string | null;
    conversations: number;
  } | null;
  email: { subscribed: boolean; last_email_at: string | null } | null;
}

export interface MessagingTimelineEntry {
  kind: string;
  title: string;
  at: string;
}

export interface Messaging360 {
  channels: MessagingChannels;
  timeline: MessagingTimelineEntry[];
  unavailable: string[];
}

export function getMessaging360(contactId: string): Promise<Messaging360> {
  return apiRequest<Messaging360>(`/customers/${encodeURIComponent(contactId)}/360`);
}
