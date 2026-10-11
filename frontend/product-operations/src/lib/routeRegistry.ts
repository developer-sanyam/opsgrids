/**
 * The app's real route table, as the mobile scan flows see it (inventory Phase 9, ADR-0027).
 *
 * An action that depends on a phase that may not be built here — receiving against a GRN,
 * inspecting — links into that phase's screen **only when this build has it**: `main.tsx` calls
 * `registerRoute` for every route it mounts, and `hasRoute` answers from that, so there is no
 * second list to keep in step. When a later phase adds `quality/inspections`, the Inspect button
 * appears with no change here; until then it is simply not drawn, never a dead link.
 */

const registered = new Set<string>();

/** Normalises `"/quality/inspections/"` and `"quality/inspections"` to one form. */
function normalise(path: string): string {
  const trimmed = path.replace(/^\/+|\/+$/g, "");
  return `/${trimmed}`;
}

/** Called by the router setup for each mounted route (relative or absolute, `:params` allowed). */
export function registerRoute(path: string): void {
  registered.add(normalise(path));
}

/** Whether a concrete path (no params) is a route this build mounts. */
export function hasRoute(path: string): boolean {
  const target = normalise(path);
  if (registered.has(target)) return true;
  const parts = target.split("/");
  for (const pattern of registered) {
    const segments = pattern.split("/");
    if (segments.length !== parts.length) continue;
    if (segments.every((s, i) => s.startsWith(":") || s === parts[i])) return true;
  }
  return false;
}

/**
 * The first of `candidates` this build mounts as *that exact route*, or `null`. Exact, not
 * pattern-matched: `/inventory/issue` is not a screen just because `inventory/:id` would answer
 * it with an item that does not exist.
 */
export function firstRoute(candidates: readonly string[]): string | null {
  return candidates.find((c) => registered.has(normalise(c))) ?? null;
}

/** Test seam: forget every registration. */
export function resetRoutes(): void {
  registered.clear();
}
