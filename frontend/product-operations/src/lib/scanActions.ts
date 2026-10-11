/**
 * Which buttons the scan screen draws for a resolved label (inventory Phase 9, ADR-0027).
 *
 * Two filters, both required: the **server** says what this caller may do with this kind of
 * record (`allowed_actions`, from their permissions), and this build says what it can actually
 * *do* — an in-page operation (transfer, status change) is always buildable, a link into another
 * phase's screen exists only when `routeRegistry` knows the route. An action failing either test
 * is not drawn. (A button is never authorization — every endpoint checks its own permission.)
 */

import type { ScanAction, ScanEntityOut } from "@/api/scan";
import { firstRoute } from "@/lib/routeRegistry";

export type ScanActionPlan =
  | { action: ScanAction; label: string; hint: string; kind: "panel" }
  | { action: ScanAction; label: string; hint: string; kind: "link"; to: string };

/** In-page operations: reuse the existing stock endpoints, no other phase needed. */
const PANELS: Partial<Record<ScanAction, { label: string; hint: string }>> = {
  transfer: { label: "Move", hint: "Bin to bin in the same warehouse" },
  status_change: { label: "Hold or release", hint: "Quality hold, quarantine, back to available" },
};

/** Links into screens other phases own, tried in order; first one this build mounts wins. */
const LINKS: Partial<
  Record<ScanAction, { label: string; hint: string; candidates: readonly string[] }>
> = {
  receive: {
    label: "Receive",
    hint: "Receive against a purchase order",
    candidates: ["/receiving", "/goods-receipts", "/purchase-orders"],
  },
  pick: {
    label: "Pick",
    hint: "Open the pick tasks",
    candidates: ["/fulfilment/tasks"],
  },
  count: {
    label: "Count",
    hint: "Open the cycle counts",
    candidates: ["/counting"],
  },
  inspect: {
    label: "Inspect",
    hint: "Open the quality inspections",
    candidates: ["/quality/inspections"],
  },
  issue: {
    label: "Issue",
    hint: "Issue to production or a job",
    candidates: ["/inventory/issue", "/manufacturing/orders"],
  },
};

/** Where "view" goes for each kind of record. */
export function viewRoute(entity: ScanEntityOut): string | null {
  if (entity.type === "location") {
    return entity.warehouse_id ? `/warehouses/${entity.warehouse_id}/locations` : null;
  }
  return entity.inventory_item_id ? `/inventory/${entity.inventory_item_id}` : null;
}

export function planActions(
  entity: ScanEntityOut,
  allowed: readonly ScanAction[],
): ScanActionPlan[] {
  const plans: ScanActionPlan[] = [];
  for (const action of allowed) {
    if (action === "view") {
      const to = viewRoute(entity);
      if (to) plans.push({ action, label: "Details", hint: "Open the full record", kind: "link", to });
      continue;
    }
    const panel = PANELS[action];
    if (panel) {
      // Moving or holding needs stock to move: nothing on hand, nothing to offer.
      if (entity.type !== "location" && entity.on_hand_qty !== "0") {
        plans.push({ action, ...panel, kind: "panel" });
      }
      continue;
    }
    const link = LINKS[action];
    if (link) {
      const to = firstRoute(link.candidates);
      if (to) plans.push({ action, label: link.label, hint: link.hint, kind: "link", to });
    }
  }
  return plans;
}
