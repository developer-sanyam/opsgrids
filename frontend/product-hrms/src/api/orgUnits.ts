/**
 * Org-unit (department) tree — `GET /api/v1/org-units` on the account service.
 *
 * The endpoint is permission-gated (`core.org.unit.read`) and returns the department tree as
 * nested `OrgUnitNode` roots. It may return **403** (the caller can't read units) or an **empty
 * list** (no units seeded yet) for an org — both are normal, so this resolves to an empty map
 * rather than throwing, and a 4xx is never retried. Callers flatten the tree to an `id -> name`
 * map and use it to label employees whose `department_id` points into it; where the map is empty
 * they fall back to the employee's `job_title` (see `@/data/departments`). This is the
 * forward-looking source of truth once org-units are populated.
 */

import { apiRequest } from "@opsgrids/client";
import { USE_FIXTURES } from "./client";

/** Mirrors account-service `OrgUnitNode`: a unit plus its nested children, one call for the tree. */
export interface OrgUnitNode {
  id: string;
  name: string;
  code: string;
  parent_id: string | null;
  head_membership_id: string | null;
  kind: string;
  is_active: boolean;
  children: OrgUnitNode[];
}

/** Walk the tree depth-first into a flat `id -> name` map. */
export function flattenOrgUnits(roots: readonly OrgUnitNode[]): Map<string, string> {
  const byId = new Map<string, string>();
  const walk = (node: OrgUnitNode): void => {
    byId.set(node.id, node.name);
    for (const child of node.children) walk(child);
  };
  for (const root of roots) walk(root);
  return byId;
}

/**
 * The department tree flattened to `id -> name`. Resolves to an **empty map** when the caller
 * lacks `core.org.unit.read` (403), when no units are seeded, or on any other read failure — a
 * missing tree is a state the UI degrades through, never an error to surface. Never retries: one
 * request, then the map (populated or empty).
 */
export async function getOrgUnitNameById(): Promise<Map<string, string>> {
  // The fixtures org has no org-units seeded (mirrors the live demo org), so the `job_title`
  // fallback is what labels departments there.
  if (USE_FIXTURES) return new Map();
  try {
    const roots = await apiRequest<OrgUnitNode[]>("/org-units", {
      signalUnauthorized: false,
    });
    return flattenOrgUnits(roots);
  } catch {
    // A 403 (`ApiError`), an empty org, or any transient read failure degrades to no live names —
    // never a retry, never a thrown error the dashboard would have to render.
    return new Map();
  }
}
