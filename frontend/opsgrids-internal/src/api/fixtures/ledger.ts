/**
 * The credit-ledger fixture — `CreditLedgerEntryAdminOut`, exactly what `GET /admin/credit-ledger`
 * returns: append-only entries constrained to `entry_type in ('grant', 'consume', 'reversal',
 * 'expiry')` at the database. No organization name, no actor and no `is_comp` flag exist in this
 * schema — "who is on comp" is not a distinction the live ledger can make, so the console can't
 * either (see `routes/Grants.tsx`).
 */

import type { CreditLedgerEntryAdminOut, ListLedgerParams, Page } from "@/api/types";
import { rupees } from "./catalog";

const INR = "INR";

export const CREDIT_LEDGER: CreditLedgerEntryAdminOut[] = [
  {
    id: "le_lotus_grant_01",
    organization_id: "org_7e3dd9",
    entry_type: "grant",
    amount_minor: rupees(360000),
    currency: INR,
    reason: "Migration goodwill — 3 months complimentary Scale",
    valid_from: "2026-08-01T00:00:00Z",
    valid_until: "2026-10-31T23:59:59Z",
    reversal_of: null,
    drawn_from: null,
    created_at: "2026-07-29T11:12:00Z",
  },
  {
    id: "le_blueoak_grant_01",
    organization_id: "org_07c662",
    entry_type: "grant",
    amount_minor: rupees(45000),
    currency: INR,
    reason: "Trial-linked comp access while evaluating CRM",
    valid_from: "2026-08-06T00:00:00Z",
    valid_until: "2026-09-05T23:59:59Z",
    reversal_of: null,
    drawn_from: null,
    created_at: "2026-08-06T09:40:00Z",
  },
  {
    id: "le_rajdhani_grant_01",
    organization_id: "org_c3d22e",
    entry_type: "grant",
    amount_minor: rupees(135000),
    currency: INR,
    reason: "Onboarding comp — 3 months Growth",
    valid_from: "2025-05-01T00:00:00Z",
    valid_until: "2025-07-31T23:59:59Z",
    reversal_of: null,
    drawn_from: null,
    created_at: "2025-04-28T15:02:00Z",
  },
  {
    id: "le_northwind_grant_01",
    organization_id: "org_a1f09c",
    entry_type: "grant",
    amount_minor: rupees(50000),
    currency: INR,
    reason: "SLA credit for Q1 settlement-desk downtime",
    valid_from: "2026-06-01T00:00:00Z",
    valid_until: null,
    reversal_of: null,
    drawn_from: null,
    created_at: "2026-06-02T10:18:00Z",
  },
  {
    id: "le_deccan_grant_01",
    organization_id: "org_e5b440",
    entry_type: "grant",
    amount_minor: rupees(100000),
    currency: INR,
    reason: "Negotiated annual prepay credit",
    valid_from: "2026-03-01T00:00:00Z",
    valid_until: null,
    reversal_of: null,
    drawn_from: null,
    created_at: "2026-02-27T08:55:00Z",
  },
  {
    id: "le_kaveri_consume_01",
    organization_id: "org_d4c33f",
    entry_type: "consume",
    amount_minor: rupees(-9000),
    currency: INR,
    reason: "Applied to August invoice INV-2026-0841",
    valid_from: "2026-08-01T00:00:00Z",
    valid_until: null,
    reversal_of: null,
    drawn_from: "le_northwind_grant_01",
    created_at: "2026-08-01T02:00:00Z",
  },
  {
    id: "le_pioneer_grant_01",
    organization_id: "org_3af995",
    entry_type: "grant",
    amount_minor: rupees(20000),
    currency: INR,
    reason: "Goodwill during dunning — retention hold",
    valid_from: "2026-08-10T00:00:00Z",
    valid_until: null,
    reversal_of: null,
    drawn_from: null,
    created_at: "2026-08-10T16:45:00Z",
  },
  {
    id: "le_pioneer_reversal_01",
    organization_id: "org_3af995",
    entry_type: "reversal",
    amount_minor: rupees(-20000),
    currency: INR,
    reason: "Reversed — customer's payment recovered",
    valid_from: "2026-08-19T00:00:00Z",
    valid_until: null,
    reversal_of: "le_pioneer_grant_01",
    drawn_from: null,
    created_at: "2026-08-19T09:22:00Z",
  },
];

export function ledgerForOrg(organizationId: string): CreditLedgerEntryAdminOut[] {
  return CREDIT_LEDGER.filter((e) => e.organization_id === organizationId);
}

/** `GET /admin/credit-ledger` in fixtures mode: the same filters and the same `Page<T>` envelope
 *  the live billing route returns. No free-text search — the live route doesn't have one either. */
export function buildLedgerPage(params: ListLedgerParams): Page<CreditLedgerEntryAdminOut> {
  const filtered = CREDIT_LEDGER.filter((e) => {
    if (params.organizationId && e.organization_id !== params.organizationId) return false;
    if (params.currency && e.currency !== params.currency.toUpperCase()) return false;
    if (params.entryType && e.entry_type !== params.entryType) return false;
    return true;
  }).sort((a, b) => b.created_at.localeCompare(a.created_at));

  const size = params.size ?? 200;
  const page = params.page ?? 1;
  const start = (page - 1) * size;
  const items = filtered.slice(start, start + size);
  return {
    items,
    total: filtered.length,
    page,
    size,
    pages: filtered.length ? Math.ceil(filtered.length / size) : 0,
  };
}
