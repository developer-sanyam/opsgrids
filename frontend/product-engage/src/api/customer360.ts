/**
 * Customer 360 — `GET /customers/{id}/360` in the `customer` service, behind the gateway's
 * `/api/v1/customers` prefix (the `crm` entitlement).
 *
 * The service aggregates other services' internal endpoints with short timeouts and returns
 * **partial results**: a section that failed or is not entitled is `null`, and its name is in
 * `unavailable`. Both are read as "Not available" by the panel. `phone` and `email` are always
 * masked; this endpoint never unmasks and nothing here reconstructs a real value.
 */

import { apiRequest } from "@opsgrids/client";

export interface Money {
  amount_minor: number;
  currency: string;
}

export interface Customer360Profile {
  id: string;
  name: string;
  company: string | null;
  since: string;
  status: "active" | "inactive";
  tags: string[];
  owner: { id: string; name: string } | null;
  /** Masked by the service. */
  phone: string;
  /** Masked by the service. */
  email: string;
  city: string | null;
}

export interface Customer360Crm {
  stage: string | null;
  open_opportunities: number;
  hot: boolean;
}

export interface Customer360Orders {
  count_90d: number;
  total_90d: Money;
  latest: { number: string; status: string; total: Money; at: string } | null;
}

export interface Customer360Finance {
  outstanding: Money;
  overdue_count: number;
  latest_invoice: { number: string; status: string; due_on: string } | null;
}

export interface Customer360Support {
  open_tickets: number;
  total_tickets: number;
}

export interface Customer360WhatsApp {
  opted_in: boolean;
  window_open_until: string | null;
  last_message_at: string | null;
  conversations: number;
}

export interface Customer360Email {
  subscribed: boolean;
  last_email_at: string | null;
}

export interface Customer360Channels {
  whatsapp: Customer360WhatsApp | null;
  email: Customer360Email | null;
}

export interface Customer360TimelineEntry {
  kind: string;
  title: string;
  at: string;
}

export interface Customer360 {
  customer: Customer360Profile;
  crm: Customer360Crm | null;
  orders: Customer360Orders | null;
  finance: Customer360Finance | null;
  support: Customer360Support | null;
  channels: Customer360Channels;
  timeline: Customer360TimelineEntry[];
  /** Names of the sections that came back `null`. */
  unavailable: string[];
}

export function getCustomer360(customerId: string): Promise<Customer360> {
  return apiRequest<Customer360>(`/customers/${encodeURIComponent(customerId)}/360`);
}
