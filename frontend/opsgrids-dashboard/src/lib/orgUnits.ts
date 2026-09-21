import type { OrgUnit } from "@/api/types";

export interface FlatUnit {
  unit: OrgUnit;
  depth: number;
}

/** Pre-order walk of the tree, each node paired with its depth — for an indented flat list or a select. */
export function flattenOrgUnits(units: OrgUnit[], depth = 0): FlatUnit[] {
  const out: FlatUnit[] = [];
  for (const unit of units) {
    out.push({ unit, depth });
    out.push(...flattenOrgUnits(unit.children, depth + 1));
  }
  return out;
}

/** `id` and everything under it — the set a "move" or "choose a new parent" picker must exclude. */
export function descendantIds(units: OrgUnit[], id: string): Set<string> {
  const out = new Set<string>();
  function find(list: OrgUnit[]): OrgUnit | null {
    for (const unit of list) {
      if (unit.id === id) return unit;
      const found = find(unit.children);
      if (found) return found;
    }
    return null;
  }
  function collect(unit: OrgUnit) {
    for (const child of unit.children) {
      out.add(child.id);
      collect(child);
    }
  }
  const root = find(units);
  if (root) collect(root);
  return out;
}

/** Every unit that has at least one active child or member — a deactivation the server will refuse. */
export function findUnit(units: OrgUnit[], id: string): OrgUnit | null {
  for (const unit of units) {
    if (unit.id === id) return unit;
    const found = findUnit(unit.children, id);
    if (found) return found;
  }
  return null;
}
