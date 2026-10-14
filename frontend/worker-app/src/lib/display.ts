import type { PickTaskLineOut, PickTaskOut } from "@/api/types";
import { formatQty } from "@/lib/quantity";
import { shortId } from "@/lib/shortId";

/**
 * What a picker reads on a task. The server resolves these inside `operations` and attaches them
 * only to a task assigned to the caller (ADR-0024 §7 addendum), so any of them can be `null` — a
 * task that is not theirs, or an older server. Every helper here falls back to the shortened id
 * rather than showing nothing, and none of them is ever used to decide anything: ids stay the
 * identity, these are labels.
 */

/** The human order number, or the shortened order id when the server did not send one. */
export function orderLabel(task: Pick<PickTaskOut, "order_number" | "order_id">): string {
  return task.order_number ?? shortId(task.order_id);
}

/** The bin's code ("A-01-03"), or the shortened location id. */
export function locationCode(code: string | null | undefined, id: string): string {
  return code ? code : shortId(id);
}

/** "A-01-03 · Rack A, bay 1" when the bin has a name, the bare code otherwise. */
export function locationLabel(
  code: string | null | undefined,
  name: string | null | undefined,
  id: string,
): string {
  const base = locationCode(code, id);
  return name ? `${base} · ${name}` : base;
}

/** The pick line's source bin, labelled. */
export function sourceLabel(line: PickTaskLineOut): string {
  return locationLabel(line.source_location_code, line.source_location_name, line.source_location_id);
}

/** What to call the item: its name, else its SKU. */
export function itemTitle(line: Pick<PickTaskLineOut, "item_name" | "sku">): string {
  return line.item_name || line.sku;
}

/** A quantity with its unit when there is one: "12.5 m". */
export function qtyWithUnit(qty: string, unit: string | null | undefined): string {
  return unit ? `${formatQty(qty)} ${unit}` : formatQty(qty);
}
