/**
 * Which controls a production order offers. Presentation only — the server checks the status and
 * the permission again on every call — but one pure function keeps the action bar, the tests and
 * the spec's status flow (DRAFT -> APPROVED -> IN_PROGRESS -> COMPLETED, or CANCELLED) in one place.
 */

import type { ProductionOrderDetailOut, ProductionOrderStatus } from "@/api/types";
import { qtyToMilli } from "@/lib/quantity";

export const PRODUCTION_PERMISSIONS = {
  read: "operations.manufacturing.production_order.read",
  write: "operations.manufacturing.production_order.write",
  approve: "operations.manufacturing.production_order.approve",
  issue: "operations.manufacturing.production_order.issue",
  complete: "operations.manufacturing.production_order.complete",
} as const;

export type ProductionAction =
  | "edit"
  | "approve"
  | "start"
  | "reserve"
  | "issue"
  | "output"
  | "wastage"
  | "return"
  | "complete"
  | "cancel";

export interface ProductionGrants {
  write: boolean;
  approve: boolean;
  issue: boolean;
  complete: boolean;
}

/** The actions this order offers this user, in the order the bar shows them. */
export function availableActions(
  order: Pick<ProductionOrderDetailOut, "status" | "materials" | "picks">,
  grants: ProductionGrants,
): ProductionAction[] {
  const status: ProductionOrderStatus = order.status;
  const actions: ProductionAction[] = [];
  const open = status === "DRAFT" || status === "APPROVED" || status === "IN_PROGRESS";
  if (!open) return actions;

  const hasOpenNeed = order.materials.some((m) => qtyToMilli(m.open_need) > 0);
  const hasWip = order.picks.some((p) => qtyToMilli(p.wip) > 0);

  if (status === "DRAFT") {
    if (grants.approve) actions.push("approve");
    if (grants.write) actions.push("edit");
  }
  if (status === "APPROVED" && grants.issue) actions.push("start");
  if ((status === "APPROVED" || status === "IN_PROGRESS") && grants.issue) {
    if (hasOpenNeed) actions.push("reserve");
    actions.push("issue");
  }
  if (status === "IN_PROGRESS") {
    if (grants.complete) actions.push("output");
    if (grants.issue && hasWip) actions.push("wastage", "return");
    if (grants.complete) actions.push("complete");
  }
  if (grants.write) actions.push("cancel");
  return actions;
}

/** True when any material is still sitting in WIP — what makes Complete ask what to do with it. */
export function hasLeftoverWip(order: Pick<ProductionOrderDetailOut, "picks">): boolean {
  return order.picks.some((p) => qtyToMilli(p.wip) > 0);
}

/** The wastage types a person can record. LEFTOVER is written by the system when an order is
 *  completed with `leftover: "scrap"`, never by hand. */
export const MANUAL_WASTAGE_TYPES = ["CUTTING", "DEFECT", "SPILLAGE", "DAMAGE", "PROCESS_LOSS", "OTHER"] as const;
