import { ApiError, apiRequest } from "@opsgrids/client";

import { USE_FIXTURES } from "@/api/client";
import type { OrgUnitNode } from "@/api/orgUnits";

function sameName(name: string): string {
  return name.trim().replace(/\s+/g, " ").toLowerCase();
}

/**
 * Every department in the tree as `(label, id)` pairs for a lookup: each department by name, and
 * each by its code, which the organisation keeps unique.
 *
 * A name that more than one department carries is written "Parent / Child", so the label picks
 * out one department. Two root departments with the same name have no parent to tell them apart,
 * so those are written by code alone; the shared name stays in the lookup, and is refused as
 * ambiguous, so it never lands on the wrong one. The first label given for a department is the
 * one an export writes back, so it is always one that resolves.
 */
export function departmentEntries(
  roots: readonly OrgUnitNode[],
): { label: string; value: string }[] {
  const units: { id: string; name: string; code: string; parent: string | null }[] = [];
  const walk = (node: OrgUnitNode, parent: string | null): void => {
    units.push({ id: node.id, name: node.name, code: node.code, parent });
    for (const child of node.children) walk(child, node.name);
  };
  for (const root of roots) walk(root, null);

  const uses = new Map<string, number>();
  for (const unit of units) {
    const key = sameName(unit.name);
    uses.set(key, (uses.get(key) ?? 0) + 1);
  }
  const named = units.map((unit) => {
    const repeated = (uses.get(sameName(unit.name)) ?? 0) > 1;
    const label = repeated && unit.parent !== null ? `${unit.parent} / ${unit.name}` : unit.name;
    return { unit, label };
  });
  const written = new Map<string, number>();
  for (const { label } of named) written.set(sameName(label), (written.get(sameName(label)) ?? 0) + 1);
  const shared = (label: string): boolean => (written.get(sameName(label)) ?? 0) > 1;

  return [
    ...named.map(({ unit, label }) => ({ value: unit.id, label: shared(label) ? unit.code : label })),
    ...named.map(({ unit, label }) => ({ value: unit.id, label: shared(label) ? label : unit.code })),
  ];
}

/**
 * `GET /org-units`, the whole department tree. A caller without `core.org.unit.read` (403) gets an
 * empty tree, so the import still runs and only a row that names a department is refused; any
 * other failure rejects, because a transient error must not read as "no such department" on every
 * row. A 401 is left to the client, which refreshes the session once and repeats the request.
 * Fixture mode has no org units, as in `getOrgUnitNameById`.
 */
export async function loadOrgUnitTree(): Promise<OrgUnitNode[]> {
  if (USE_FIXTURES) return [];
  try {
    return await apiRequest<OrgUnitNode[]>("/org-units");
  } catch (error) {
    if (error instanceof ApiError && error.status === 403) return [];
    throw error;
  }
}
