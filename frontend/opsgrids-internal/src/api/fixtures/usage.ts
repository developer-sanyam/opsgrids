/**
 * Usage roll-ups fixture — `UsageOut`, exactly what `GET /admin/usage` returns: a meter key, a
 * period label and a raw quantity. No meter name, unit, included allowance or billability flag —
 * those live on the plan version and the meter catalogue, which this projection does not join.
 */

import type { UsageListOut, UsageOut } from "@/api/types";
import { subscriptionsForOrg, allOrgIds } from "./orgs";

const METER_KEY: Record<string, string> = {
  hrms: "hrms.payslip.generated",
  crm: "crm.deal.created",
  auctions: "auctions.lot.published",
  procure: "procure.po.raised",
  trading: "trading.trade.booked",
};

const PER_SEAT: Record<string, number> = {
  hrms: 1,
  crm: 6,
  auctions: 14,
  procure: 9,
  trading: 22,
};

const PERIOD = "2026-08";

function usageForOrg(organizationId: string): UsageOut[] {
  return subscriptionsForOrg(organizationId)
    .map((sub): UsageOut | null => {
      const meterKey = METER_KEY[sub.product_key];
      if (!meterKey) return null;
      const damp = sub.state === "suspended" || sub.state === "cancelled" ? 0.35 : 1;
      const quantity = Math.round(sub.seats * (PER_SEAT[sub.product_key] ?? 1) * damp);
      return { organization_id: organizationId, meter_key: meterKey, period: PERIOD, quantity };
    })
    .filter((u): u is UsageOut => u !== null);
}

export function buildUsageListOut(): UsageListOut {
  return { usage: allOrgIds().flatMap(usageForOrg) };
}
