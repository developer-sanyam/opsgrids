/**
 * The credit-ledger fixture (ADR-0004 §3): append-only entries — grants (positive), consumption
 * (negative), and reversals (pointing at the entry they undo). Comp access and monetary credit are
 * both entries with a validity window; "who is on comp, and when does it lapse" is a query over
 * these rows whose window covers now, never a status column. Balance is a SUM, never stored.
 */

import type { CreditLedgerEntry } from "@/api/types";
import { rupees } from "./catalog";

const INR = "INR";

export const CREDIT_LEDGER: CreditLedgerEntry[] = [
  {
    entry_id: "le_lotus_comp_01",
    organization_id: "org_7e3dd9",
    organization_name: "Lotus Manufacturing",
    amount_minor: rupees(360000),
    currency: INR,
    kind: "grant",
    reason: "Migration goodwill — 3 months complimentary Scale",
    valid_from: "2026-08-01T00:00:00Z",
    valid_until: "2026-10-31T23:59:59Z",
    created_at: "2026-07-29T11:12:00Z",
    actor: "Priya Venkatesan",
    reversal_of: null,
    is_comp: true,
  },
  {
    entry_id: "le_blueoak_comp_01",
    organization_id: "org_07c662",
    organization_name: "BlueOak Retail",
    amount_minor: rupees(45000),
    currency: INR,
    kind: "grant",
    reason: "Trial-linked comp access while evaluating CRM",
    valid_from: "2026-08-06T00:00:00Z",
    valid_until: "2026-09-05T23:59:59Z",
    created_at: "2026-08-06T09:40:00Z",
    actor: "Rahul Menon",
    reversal_of: null,
    is_comp: true,
  },
  {
    entry_id: "le_rajdhani_comp_01",
    organization_id: "org_c3d22e",
    organization_name: "Rajdhani Traders",
    amount_minor: rupees(135000),
    currency: INR,
    kind: "grant",
    reason: "Onboarding comp — 3 months Growth",
    valid_from: "2025-05-01T00:00:00Z",
    valid_until: "2025-07-31T23:59:59Z",
    created_at: "2025-04-28T15:02:00Z",
    actor: "Priya Venkatesan",
    reversal_of: null,
    is_comp: true,
  },
  {
    entry_id: "le_northwind_credit_01",
    organization_id: "org_a1f09c",
    organization_name: "Northwind Metals",
    amount_minor: rupees(50000),
    currency: INR,
    kind: "grant",
    reason: "SLA credit for Q1 settlement-desk downtime",
    valid_from: "2026-06-01T00:00:00Z",
    valid_until: null,
    created_at: "2026-06-02T10:18:00Z",
    actor: "Priya Venkatesan",
    reversal_of: null,
    is_comp: false,
  },
  {
    entry_id: "le_deccan_credit_01",
    organization_id: "org_e5b440",
    organization_name: "Deccan Commodities",
    amount_minor: rupees(100000),
    currency: INR,
    kind: "grant",
    reason: "Negotiated annual prepay credit",
    valid_from: "2026-03-01T00:00:00Z",
    valid_until: null,
    created_at: "2026-02-27T08:55:00Z",
    actor: "Devansh Rao",
    reversal_of: null,
    is_comp: false,
  },
  {
    entry_id: "le_meridian_credit_01",
    organization_id: "org_f6a551",
    organization_name: "Meridian HR Cloud",
    amount_minor: rupees(15000),
    currency: INR,
    kind: "grant",
    reason: "Referral credit — introduced Anta Foods",
    valid_from: "2026-07-01T00:00:00Z",
    valid_until: "2026-09-30T23:59:59Z",
    created_at: "2026-07-01T12:30:00Z",
    actor: "Rahul Menon",
    reversal_of: null,
    is_comp: false,
  },
  {
    entry_id: "le_everest_credit_01",
    organization_id: "org_905ffb",
    organization_name: "Everest Bearings",
    amount_minor: rupees(30000),
    currency: INR,
    kind: "grant",
    reason: "Volume-commitment credit — 50+ seats",
    valid_from: "2026-06-01T00:00:00Z",
    valid_until: "2026-12-31T23:59:59Z",
    created_at: "2026-05-30T14:07:00Z",
    actor: "Devansh Rao",
    reversal_of: null,
    is_comp: false,
  },
  {
    entry_id: "le_kaveri_consume_01",
    organization_id: "org_d4c33f",
    organization_name: "Kaveri Steel",
    amount_minor: rupees(-9000),
    currency: INR,
    kind: "consumption",
    reason: "Applied to August invoice INV-2026-0841",
    valid_from: "2026-08-01T00:00:00Z",
    valid_until: null,
    created_at: "2026-08-01T02:00:00Z",
    actor: "system",
    reversal_of: null,
    is_comp: false,
  },
  {
    entry_id: "le_pioneer_grant_01",
    organization_id: "org_3af995",
    organization_name: "Pioneer Alloys",
    amount_minor: rupees(20000),
    currency: INR,
    kind: "grant",
    reason: "Goodwill during dunning — retention hold",
    valid_from: "2026-08-10T00:00:00Z",
    valid_until: null,
    created_at: "2026-08-10T16:45:00Z",
    actor: "Rahul Menon",
    reversal_of: null,
    is_comp: false,
  },
  {
    entry_id: "le_pioneer_reversal_01",
    organization_id: "org_3af995",
    organization_name: "Pioneer Alloys",
    amount_minor: rupees(-20000),
    currency: INR,
    kind: "reversal",
    reason: "Reversed — customer's payment recovered",
    valid_from: "2026-08-19T00:00:00Z",
    valid_until: null,
    created_at: "2026-08-19T09:22:00Z",
    actor: "Priya Venkatesan",
    reversal_of: "le_pioneer_grant_01",
    is_comp: false,
  },
];

export function ledgerForOrg(organizationId: string): CreditLedgerEntry[] {
  return CREDIT_LEDGER.filter((e) => e.organization_id === organizationId);
}

/** True when the entry's validity window covers `at` — used for balance and the comp query. */
export function coversNow(entry: CreditLedgerEntry, at: Date = new Date("2026-08-24T00:00:00Z")): boolean {
  const from = new Date(entry.valid_from).getTime();
  const until = entry.valid_until ? new Date(entry.valid_until).getTime() : Number.POSITIVE_INFINITY;
  const t = at.getTime();
  return t >= from && t <= until;
}
