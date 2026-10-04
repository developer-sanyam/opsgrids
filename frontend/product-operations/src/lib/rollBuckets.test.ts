import { describe, expect, it } from "vitest";

import { roll, stockRow } from "@/test-utils/tracking";
import { actionableRows, commonBuckets, mergeBlocker } from "./rollBuckets";

describe("actionableRows", () => {
  it("keeps on-site, non-empty buckets only", () => {
    const rows = [
      stockRow({ id: "a" }),
      stockRow({ id: "b", on_hand_qty: "0" }),
      stockRow({ id: "c", status: "IN_TRANSIT" }),
      stockRow({ id: "d", status: "QUALITY_HOLD" }),
    ];
    expect(actionableRows(rows).map((r) => r.id)).toEqual(["a", "d"]);
  });
});

describe("commonBuckets", () => {
  const r1 = roll({ id: "r1" });
  const r2 = roll({ id: "r2" });

  it("offers a bucket only where every roll holds stock, with the combined quantity", () => {
    const rows = new Map([
      ["r1", [stockRow({ roll_id: "r1", location_id: "L1", on_hand_qty: "50" }), stockRow({ roll_id: "r1", location_id: "L2", on_hand_qty: "5" })]],
      ["r2", [stockRow({ roll_id: "r2", location_id: "L1", on_hand_qty: "32.5" })]],
    ]);
    const buckets = commonBuckets([r1, r2], rows);
    expect(buckets).toHaveLength(1);
    expect(buckets[0]).toMatchObject({ location_id: "L1", status: "AVAILABLE", perRollMilli: [50000, 32500], totalMilli: 82500 });
  });

  it("is empty when the rolls never share a location and status", () => {
    const rows = new Map([
      ["r1", [stockRow({ roll_id: "r1", location_id: "L1" })]],
      ["r2", [stockRow({ roll_id: "r2", location_id: "L2" })]],
    ]);
    expect(commonBuckets([r1, r2], rows)).toEqual([]);
    expect(commonBuckets([r1, r2], new Map())).toEqual([]);
  });

  it("does not treat the same location in a different status as shared", () => {
    const rows = new Map([
      ["r1", [stockRow({ roll_id: "r1", location_id: "L1", status: "AVAILABLE" })]],
      ["r2", [stockRow({ roll_id: "r2", location_id: "L1", status: "QUALITY_HOLD" })]],
    ]);
    expect(commonBuckets([r1, r2], rows)).toEqual([]);
  });
});

describe("mergeBlocker", () => {
  it("says why a selection cannot merge, and is null when nothing rules it out", () => {
    expect(mergeBlocker([roll()])).toBe("Select at least two rolls to merge.");
    expect(mergeBlocker([roll({ id: "a" }), roll({ id: "b" })])).toBeNull();
    expect(mergeBlocker([roll({ id: "a" }), roll({ id: "b", state: "SPLIT" })])).toMatch(/Only active rolls/);
    expect(mergeBlocker([roll({ id: "a" }), roll({ id: "b", inventory_item_id: "other" })])).toMatch(/different items/);
    expect(mergeBlocker([roll({ id: "a" }), roll({ id: "b", lot_id: "lot-2" })])).toMatch(/different lots/);
    expect(mergeBlocker([roll({ id: "a" }), roll({ id: "b", shade: "Red" })])).toMatch(/different shades/);
  });
});
