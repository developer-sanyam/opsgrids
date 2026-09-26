import { ApiError, apiRequest } from "@opsgrids/client";

import { USE_FIXTURES } from "@/api/client";
import type { OrgUnitNode } from "@/api/orgUnits";

function sameName(name: string): string {
  return name.trim().replace(/\s+/g, " ").toLowerCase();
}

/**
 * Every department in the tree as a `(label, id)` pair for a lookup. A name that more than one
 * department carries is written "Parent / Child", so each label picks out exactly one department
 * and the sheet's dropdown can tell them apart.
 */
export function departmentEntries(
  roots: readonly OrgUnitNode[],
): { label: string; value: string }[] {
  const units: { id: string; name: string; parent: string | null }[] = [];
  const walk = (node: OrgUnitNode, parent: string | null): void => {
    units.push({ id: node.id, name: node.name, parent });
    for (const child of node.children) walk(child, node.name);
  };
  for (const root of roots) walk(root, null);

  const uses = new Map<string, number>();
  for (const unit of units) {
    const key = sameName(unit.name);
    uses.set(key, (uses.get(key) ?? 0) + 1);
  }
  return units.map((unit) => {
    const repeated = (uses.get(sameName(unit.name)) ?? 0) > 1;
    return {
      value: unit.id,
      label: repeated && unit.parent !== null ? `${unit.parent} / ${unit.name}` : unit.name,
    };
  });
}

/**
 * `GET /org-units`, the whole department tree. A caller without `core.org.unit.read` (403) gets an
 * empty tree, so the import still runs and only a row that names a department is refused; any
 * other failure rejects, because a transient error must not read as "no such department" on every
 * row. Fixture mode has no org units, as in `getOrgUnitNameById`.
 */
export async function loadOrgUnitTree(): Promise<OrgUnitNode[]> {
  if (USE_FIXTURES) return [];
  try {
    return await apiRequest<OrgUnitNode[]>("/org-units", { signalUnauthorized: false });
  } catch (error) {
    if (error instanceof ApiError && error.status === 403) return [];
    throw error;
  }
}
