/**
 * Which buckets a roll operation can act on, worked out from the stock rows of the rolls involved.
 *
 * A split takes the roll's stock from one bucket (a location and a status); a merge needs every
 * source roll to hold stock in the *same* bucket. The server enforces both; computing them here
 * lets the dialog offer only buckets that can work, and say so plainly when none can.
 */

import type { OnSiteStatus, RollOut, StockRowOut } from "@/api/trackingTypes";
import { isOnSiteRow } from "@/lib/trackingLabels";
import { qtyToMilli } from "@/lib/quantity";

/** A stock row whose status a stock operation may act on. */
export type OnSiteRow = StockRowOut & { status: OnSiteStatus };

/** Rows holding stock a stock operation may act on: on site, and not empty. */
export function actionableRows(rows: readonly StockRowOut[]): OnSiteRow[] {
  return rows.filter((row): row is OnSiteRow => isOnSiteRow(row) && qtyToMilli(row.on_hand_qty) > 0);
}

/** A location and status, as one key. */
export function bucketKey(row: Pick<StockRowOut, "location_id" | "status">): string {
  return `${row.location_id}|${row.status}`;
}

export interface CommonBucket {
  key: string;
  location_id: string;
  location_code: string;
  status: OnSiteStatus;
  /** What each roll holds there, milli-units, in the order the rolls were given. */
  perRollMilli: number[];
  totalMilli: number;
}

/** Buckets in which **every** roll holds stock. Empty when the rolls never share one. */
export function commonBuckets(rolls: readonly RollOut[], rowsByRoll: ReadonlyMap<string, StockRowOut[]>): CommonBucket[] {
  const first = rolls[0];
  if (!first) return [];
  const out: CommonBucket[] = [];
  for (const candidate of actionableRows(rowsByRoll.get(first.id) ?? [])) {
    const key = bucketKey(candidate);
    const perRollMilli: number[] = [];
    for (const roll of rolls) {
      const match = actionableRows(rowsByRoll.get(roll.id) ?? []).find((row) => bucketKey(row) === key);
      if (!match) break;
      perRollMilli.push(qtyToMilli(match.on_hand_qty));
    }
    if (perRollMilli.length === rolls.length) {
      out.push({
        key,
        location_id: candidate.location_id,
        location_code: candidate.location_code,
        status: candidate.status,
        perRollMilli,
        totalMilli: perRollMilli.reduce((a, b) => a + b, 0),
      });
    }
  }
  return out;
}

/** Why a set of rolls cannot be merged, in a sentence, or `null` if nothing the client can see
 *  rules it out. The server's own refusal (`roll_ids/not_mergeable`) stays the authority. */
export function mergeBlocker(rolls: readonly RollOut[]): string | null {
  if (rolls.length < 2) return "Select at least two rolls to merge.";
  if (rolls.some((r) => r.state !== "ACTIVE")) return "Only active rolls can be merged. Remove the split or merged ones.";
  const first = rolls[0];
  if (!first) return null;
  if (rolls.some((r) => r.inventory_item_id !== first.inventory_item_id)) {
    return "These rolls are of different items. Rolls merge only within one item.";
  }
  if (rolls.some((r) => r.lot_id !== first.lot_id)) {
    return "These rolls are from different lots. Rolls merge only within one lot.";
  }
  if (rolls.some((r) => (r.shade ?? "") !== (first.shade ?? ""))) {
    return "These rolls are different shades. Rolls merge only within one shade.";
  }
  return null;
}
