/**
 * Invoice history, generated per subscription (ADR-0004: append-only, frozen at finalize). Active
 * accounts carry finalized invoices for recent months; a past-due account has an unpaid draft; a
 * suspended one has a voided latest invoice; a cancelled one has a final finalized invoice. Trials
 * have none. Amounts are the subscription's pinned monthly price in minor units.
 */

import type { Invoice, Subscription } from "@/api/types";
import { SUBSCRIPTIONS } from "./orgs";

interface Period {
  label: string;
  start: string;
  end: string;
  issued: string;
  finalized: string;
}

const PERIODS: Record<"jun" | "jul" | "aug", Period> = {
  jun: {
    label: "Jun 2026",
    start: "2026-06-01T00:00:00Z",
    end: "2026-06-30T23:59:59Z",
    issued: "2026-07-01T00:00:00Z",
    finalized: "2026-07-01T02:00:00Z",
  },
  jul: {
    label: "Jul 2026",
    start: "2026-07-01T00:00:00Z",
    end: "2026-07-31T23:59:59Z",
    issued: "2026-08-01T00:00:00Z",
    finalized: "2026-08-01T02:00:00Z",
  },
  aug: {
    label: "Aug 2026",
    start: "2026-08-01T00:00:00Z",
    end: "2026-08-31T23:59:59Z",
    issued: "2026-08-24T00:00:00Z",
    finalized: "2026-08-24T02:00:00Z",
  },
};

let counter = 800;
function nextNumber(): string {
  counter += 1;
  return `INV-2026-${counter.toString().padStart(4, "0")}`;
}

function invoice(
  sub: Subscription,
  period: Period,
  state: Invoice["state"],
): Invoice {
  return {
    invoice_id: `inv_${sub.subscription_id}_${period.label.slice(0, 3).toLowerCase()}`,
    organization_id: sub.organization_id,
    number: nextNumber(),
    state,
    currency: sub.currency,
    total_minor: state === "void" ? 0 : sub.amount_minor,
    period_start: period.start,
    period_end: period.end,
    finalized_at: state === "finalized" ? period.finalized : null,
    issued_at: period.issued,
  };
}

function invoicesForSub(sub: Subscription): Invoice[] {
  switch (sub.state) {
    case "active":
      return [
        invoice(sub, PERIODS.jun, "finalized"),
        invoice(sub, PERIODS.jul, "finalized"),
        invoice(sub, PERIODS.aug, "finalized"),
      ];
    case "past_due":
      return [
        invoice(sub, PERIODS.jun, "finalized"),
        invoice(sub, PERIODS.jul, "finalized"),
        invoice(sub, PERIODS.aug, "draft"),
      ];
    case "suspended":
      return [invoice(sub, PERIODS.jun, "finalized"), invoice(sub, PERIODS.jul, "void")];
    case "cancelled":
      return [invoice(sub, PERIODS.jun, "finalized")];
    case "trial":
    default:
      return [];
  }
}

export const INVOICES: Invoice[] = SUBSCRIPTIONS.flatMap(invoicesForSub);

export function invoicesForOrg(organizationId: string): Invoice[] {
  return INVOICES.filter((inv) => inv.organization_id === organizationId).sort((a, b) =>
    b.issued_at.localeCompare(a.issued_at),
  );
}
