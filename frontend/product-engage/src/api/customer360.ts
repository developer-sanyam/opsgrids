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

import { USE_FIXTURES, settle } from "./client";
import { CUSTOMER_360 } from "./customer360.fixtures";
export type {
  Money,
  Customer360Profile,
  Customer360Crm,
  Customer360Orders,
  Customer360Finance,
  Customer360Support,
  Customer360WhatsApp,
  Customer360Email,
  Customer360Channels,
  Customer360TimelineEntry,
  Customer360,
} from "./customer360.types";
import type { Customer360 } from "./customer360.types";

export function getCustomer360(customerId: string): Promise<Customer360> {
  if (USE_FIXTURES) return settle(CUSTOMER_360);
  return apiRequest<Customer360>(`/customers/${encodeURIComponent(customerId)}/360`);
}