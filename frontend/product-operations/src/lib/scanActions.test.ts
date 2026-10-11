/**
 * Which buttons a scan draws. The server's `allowed_actions` and this build's real route table
 * must both agree before a button appears — so an action that depends on an unbuilt phase is
 * absent, not a dead link, and appears on its own when that phase's route is mounted.
 */
import { beforeEach, describe, expect, it } from "vitest";

import type { ScanEntityOut } from "@/api/scan";
import { firstRoute, hasRoute, registerRoute, resetRoutes } from "./routeRegistry";
import { planActions } from "./scanActions";

const ROLL: ScanEntityOut = {
  type: "roll",
  id: "r1",
  code: "R-1",
  title: "R-1",
  subtitle: "COT · LOT-A",
  uom: "m",
  on_hand_qty: "82.5",
  inventory_item_id: "item-1",
  sku: "COT",
  lot_id: "lot-1",
  lot_no: "LOT-A",
  roll_id: "r1",
  location_id: null,
  warehouse_id: null,
  track_lot: true,
  track_roll: true,
  status: "ACTIVE",
};

const LOCATION: ScanEntityOut = {
  ...ROLL,
  type: "location",
  id: "loc-1",
  on_hand_qty: null,
  inventory_item_id: null,
  roll_id: null,
  lot_id: null,
  location_id: "loc-1",
  warehouse_id: "wh-1",
};

const labels = (entity: ScanEntityOut, allowed: Parameters<typeof planActions>[1]) =>
  planActions(entity, allowed).map((p) => p.label);

beforeEach(() => {
  resetRoutes();
  for (const path of ["purchase-orders", "fulfilment/tasks", "counting", "inventory/:id"]) {
    registerRoute(path);
  }
});

describe("route registry", () => {
  it("matches parameterised routes and normalises slashes", () => {
    expect(hasRoute("/inventory/abc-123")).toBe(true);
    expect(hasRoute("counting/")).toBe(true);
    expect(hasRoute("/quality/inspections")).toBe(false);
    expect(firstRoute(["/receiving", "/purchase-orders"])).toBe("/purchase-orders");
    expect(firstRoute(["/receiving"])).toBeNull();
  });
});

describe("planActions", () => {
  it("draws the in-page moves and the details link for a roll the caller can move", () => {
    expect(labels(ROLL, ["view", "transfer", "status_change"])).toEqual([
      "Details",
      "Move",
      "Hold or release",
    ]);
  });

  it("never draws what the server did not allow", () => {
    expect(labels(ROLL, ["view"])).toEqual(["Details"]);
  });

  it("hides inspect while there is no quality route, and shows it once there is", () => {
    expect(labels(ROLL, ["view", "inspect"])).toEqual(["Details"]);
    registerRoute("quality/inspections");
    expect(labels(ROLL, ["view", "inspect"])).toEqual(["Details", "Inspect"]);
    const inspect = planActions(ROLL, ["inspect"])[0];
    expect(inspect && inspect.kind === "link" && inspect.to).toBe("/quality/inspections");
  });

  it("receives against the purchase orders until a goods-receipt screen exists", () => {
    const first = planActions(LOCATION, ["receive"])[0];
    expect(first && first.kind === "link" && first.to).toBe("/purchase-orders");
    registerRoute("goods-receipts");
    const later = planActions(LOCATION, ["receive"])[0];
    expect(later && later.kind === "link" && later.to).toBe("/goods-receipts");
  });

  it("hides issue (no screen issues stock yet) rather than linking nowhere", () => {
    expect(labels(ROLL, ["issue"])).toEqual([]);
    registerRoute("manufacturing/orders");
    expect(labels(ROLL, ["issue"])).toEqual(["Issue"]);
  });

  it("links count and pick into the screens that own them", () => {
    expect(labels(LOCATION, ["view", "count"])).toEqual(["Details", "Count"]);
    expect(labels(ROLL, ["pick"])).toEqual(["Pick"]);
  });

  it("offers nothing to move when nothing is on hand", () => {
    expect(labels({ ...ROLL, on_hand_qty: "0" }, ["transfer", "status_change"])).toEqual([]);
  });

  it("sends a location's details to its warehouse's locations", () => {
    const view = planActions(LOCATION, ["view"])[0];
    expect(view && view.kind === "link" && view.to).toBe("/warehouses/wh-1/locations");
  });
});
