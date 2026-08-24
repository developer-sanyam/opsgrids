/**
 * Usage roll-ups per organization for the current period. Product services emit usage from day one
 * with `is_billable=false` (ADR-0004 §7); only the metered HRMS payslip line is billable here,
 * matching the usage price on the HRMS Scale plan version. Quantities scale off seat count so the
 * detail page stays internally consistent with the directory.
 */

import type { Subscription, UsageSummary } from "@/api/types";
import { subscriptionsForOrg } from "./orgs";

interface MeterSpec {
  meter_key: string;
  meter_name: string;
  unit: string;
  perSeat: number;
  included: number | null;
  is_billable: boolean;
}

const METERS: Record<string, MeterSpec> = {
  hrms: {
    meter_key: "hrms.payslip.generated",
    meter_name: "Payslips generated",
    unit: "payslips",
    perSeat: 1,
    included: 2000,
    is_billable: true,
  },
  crm: {
    meter_key: "crm.deal.created",
    meter_name: "Deals created",
    unit: "deals",
    perSeat: 6,
    included: null,
    is_billable: false,
  },
  auctions: {
    meter_key: "auctions.lot.published",
    meter_name: "Lots published",
    unit: "lots",
    perSeat: 14,
    included: 1000,
    is_billable: false,
  },
  procure: {
    meter_key: "procure.po.raised",
    meter_name: "Purchase orders raised",
    unit: "purchase orders",
    perSeat: 9,
    included: null,
    is_billable: false,
  },
  trading: {
    meter_key: "trading.trade.booked",
    meter_name: "Trades booked",
    unit: "trades",
    perSeat: 22,
    included: null,
    is_billable: false,
  },
};

function usageForSub(sub: Subscription): UsageSummary | null {
  const spec = METERS[sub.product_key];
  if (!spec) return null;
  // Suspended/cancelled accounts show a quieter last-recorded period.
  const damp = sub.state === "suspended" || sub.state === "cancelled" ? 0.35 : 1;
  const quantity = Math.round(sub.seats * spec.perSeat * damp);
  return {
    meter_key: spec.meter_key,
    meter_name: spec.meter_name,
    product_key: sub.product_key,
    unit: spec.unit,
    quantity,
    included: spec.included,
    is_billable: spec.is_billable,
    period_label: "Aug 2026",
  };
}

export function usageForOrg(organizationId: string): UsageSummary[] {
  return subscriptionsForOrg(organizationId)
    .map(usageForSub)
    .filter((u): u is UsageSummary => u !== null);
}
