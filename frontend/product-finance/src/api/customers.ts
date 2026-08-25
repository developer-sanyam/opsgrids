/**
 * The customer directory — `GET /api/v1/finance/customers` flattened to an `id -> Customer` map.
 *
 * Invoices and payments carry `customer_id` as a plain UUID (ADR-0000: no cross-schema joins),
 * and the live rows never embed the party. The customers-ledger endpoint does — each
 * `CustomerSummary` embeds its `Customer` — so one call yields every display record the other
 * screens need. Mirrors the HRMS org-units pattern: resolve to an **empty map** on any read
 * failure (a missing directory is a state the UI degrades through via "Unknown customer", never
 * an error to surface), and never retry a failed read.
 */

import { FIXTURE_CUSTOMERS } from "@/data/customers";
import { listCustomers, USE_FIXTURES } from "./client";
import type { Customer } from "./types";

/**
 * The customers this org bills, keyed by id. In fixture mode this is the static reference list;
 * live it is built from the customers ledger. Degrades to an empty map on any failure.
 */
export async function getCustomerById(): Promise<ReadonlyMap<string, Customer>> {
  if (USE_FIXTURES) return FIXTURE_CUSTOMERS;
  try {
    const page = await listCustomers({ sort: "name_asc" });
    return new Map(page.items.map((row) => [row.customer.id, row.customer]));
  } catch {
    // A 403, an empty org, or a transient read failure degrades to no resolved names — the
    // screens fall back to "Unknown customer" rather than erroring. Never retried.
    return new Map();
  }
}
