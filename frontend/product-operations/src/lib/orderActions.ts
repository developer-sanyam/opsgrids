import type { OrderStatus } from "@/api/types";

/**
 * Which lifecycle controls an order offers, by status and by whether the viewer holds the
 * `accept`/`cancel` permissions at `ORG` scope — mirroring `ORDER_TRANSITIONS`
 * (`services/operations/app/domain.py`, ADR-0025 §1) so an operator is never shown a control that
 * would just 409. The service remains the thing that enforces it; this only decides what to draw.
 *
 * `place`/`hold`/`release` sit behind `operations.orders.order.write`, the same as the purchase
 * order's plain-CRUD actions; `accept`/`cancel` need the stricter `:ORG` scope (F1's
 * `layout.publish` split is the precedent this mirrors) since accepting commits real stock and
 * cancelling releases it — both dangerous enough that the catalogue floors them at the org level
 * with no `OWN` reading at all.
 *
 * `IN_FULFILMENT`/`READY`/`HANDED_OVER`/`DELIVERED`/`COMPLETED` are reachable states this phase's
 * UI does not drive into (F3–F5 do), but an order can still arrive at one from a direct API call
 * or a later phase once it ships; `hold`/`cancel` stay offered exactly where ADR-0025's own table
 * allows them, so this file needs no update when F3 lands its own screens.
 */
export interface OrderActions {
  place: boolean;
  accept: boolean;
  hold: boolean;
  release: boolean;
  cancel: boolean;
}

export function orderActions(status: OrderStatus, canAccept: boolean, canCancel: boolean): OrderActions {
  return {
    place: status === "DRAFT",
    accept: status === "PLACED" && canAccept,
    hold: status === "ACCEPTED" || status === "IN_FULFILMENT" || status === "READY",
    release: status === "ON_HOLD",
    cancel:
      canCancel &&
      (status === "DRAFT" ||
        status === "PLACED" ||
        status === "ACCEPTED" ||
        status === "IN_FULFILMENT" ||
        status === "ON_HOLD" ||
        status === "READY"),
  };
}
