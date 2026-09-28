/**
 * Pure tree helpers shared by the warehouse location tree screen and its print-labels route —
 * `Location.parent_id` is the only structure the API gives us (Fulfilment plan F1); everything
 * about depth, order and a zone's subtree is derived here, once, rather than in each screen.
 */
import type { LocationOut } from "@/api/types";

export interface LocationRow {
  location: LocationOut;
  depth: number;
}

/** The 4px-scale indent per tree depth (`opsgrids/tokens`): 0, 16, 32, 48, 64px. Anything deeper
 *  than the vocabulary's real nesting (zone → aisle → rack → shelf → bin) reuses the last step
 *  rather than growing without bound. */
const INDENT_CLASSES = ["pl-0", "pl-4", "pl-8", "pl-12", "pl-16"];

export function indentClass(depth: number): string {
  return INDENT_CLASSES[Math.min(depth, INDENT_CLASSES.length - 1)] ?? "pl-0";
}

/**
 * Depth-first flatten of the parent/child tree, ordered within each parent by `pick_sequence`
 * (nulls last) then `code` — the same order a picker's walk and a printed label sheet both want.
 * `seen` guards against a cycle in bad data rather than recursing forever: the API's own tree
 * should never produce one, but a screen that hangs on malformed data is worse than one that
 * silently stops descending it.
 */
export function flattenTree(locations: LocationOut[]): LocationRow[] {
  const byParent = new Map<string | null, LocationOut[]>();
  for (const location of locations) {
    const list = byParent.get(location.parent_id) ?? [];
    list.push(location);
    byParent.set(location.parent_id, list);
  }
  for (const list of byParent.values()) {
    list.sort((a, b) => {
      const seqA = a.pick_sequence ?? Number.MAX_SAFE_INTEGER;
      const seqB = b.pick_sequence ?? Number.MAX_SAFE_INTEGER;
      return seqA - seqB || a.code.localeCompare(b.code);
    });
  }

  const rows: LocationRow[] = [];
  const seen = new Set<string>();
  function visit(parentId: string | null, depth: number): void {
    for (const location of byParent.get(parentId) ?? []) {
      if (seen.has(location.id)) continue;
      seen.add(location.id);
      rows.push({ location, depth });
      visit(location.id, depth + 1);
    }
  }
  visit(null, 0);
  return rows;
}

/** Every location in `zoneId`'s own subtree, zone included — what "print labels" for a zone
 *  hands to the label sheet. `[]` when `zoneId` doesn't match any row (a stale link). */
export function zoneSubtree(rows: LocationRow[], zoneId: string): LocationOut[] {
  const byId = new Map(rows.map((r) => [r.location.id, r.location]));
  const zone = byId.get(zoneId);
  if (!zone) return [];
  const childrenOf = new Map<string | null, LocationOut[]>();
  for (const r of rows) {
    const list = childrenOf.get(r.location.parent_id) ?? [];
    list.push(r.location);
    childrenOf.set(r.location.parent_id, list);
  }
  const out: LocationOut[] = [];
  const seen = new Set<string>();
  function visit(location: LocationOut): void {
    if (seen.has(location.id)) return;
    seen.add(location.id);
    out.push(location);
    for (const child of childrenOf.get(location.id) ?? []) visit(child);
  }
  visit(zone);
  return out;
}
