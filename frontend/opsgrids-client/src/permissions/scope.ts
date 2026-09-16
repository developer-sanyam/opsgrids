/**
 * The scope ladder, mirrored from the server so a hidden control and a refused call agree.
 *
 * The grammar is `product.module.resource.action:SCOPE` (ADR-0000 §5) on the ladder
 * `OWN < TEAM < LOCATION < ORG` (§6). `platform_core.auth` resolves a grant exactly this way, and
 * the two must not drift — but note which direction the risk runs in: this file being *wrong*
 * shows or hides a button, while the server being wrong lets data out. The server is the
 * authority; this is a mirror of it for the sake of the interface.
 *
 * Two rules are copied here deliberately, because both have caused real bugs:
 *
 * - **A permission name without enough scope is not access.** Every employee holds
 *   `…employee.write:OWN`, so checking the name alone would show an org-wide control to everyone.
 *   Pass the `minimum` the endpoint's guard passes.
 * - **`read` never implies `read_sensitive`.** Codes match exactly; no prefix matching, ever.
 */

export type Scope = "OWN" | "TEAM" | "LOCATION" | "ORG";

const LADDER: readonly Scope[] = ["OWN", "TEAM", "LOCATION", "ORG"];

/** Where a scope sits on the ladder. `-1` for anything that is not a scope. */
export function scopeRank(scope: string): number {
  return LADDER.indexOf(scope as Scope);
}

interface ParsedGrant {
  code: string;
  scope: Scope;
}

/**
 * Split `hrms.people.employee.read:ORG`. A grant with no `:SCOPE` suffix is read as `OWN` — the
 * narrowest reading, which is the safe one for a control that would otherwise be shown.
 */
export function parseGrant(grant: string): ParsedGrant | null {
  const separator = grant.lastIndexOf(":");
  if (separator === -1) return grant ? { code: grant, scope: "OWN" } : null;
  const code = grant.slice(0, separator);
  const scope = grant.slice(separator + 1);
  if (!code) return null;
  return { code, scope: scopeRank(scope) === -1 ? "OWN" : (scope as Scope) };
}

/**
 * Does this set of grants carry `code` at `minimum` or above?
 *
 * An empty or unknown grant set answers `false`: where the client cannot tell, it hides the
 * control and lets the server answer. A hidden control the caller could have used is a small
 * cost; a shown control that 403s at the end of a form is a larger one.
 */
export function hasPermission(
  grants: readonly string[],
  code: string,
  minimum: Scope = "OWN",
): boolean {
  const floor = scopeRank(minimum);
  for (const grant of grants) {
    const parsed = parseGrant(grant);
    if (!parsed) continue;
    if (parsed.code !== code) continue;
    if (scopeRank(parsed.scope) >= floor) return true;
  }
  return false;
}

/** The widest scope held for a code, or `null` when it is not held at all. */
export function scopeFor(grants: readonly string[], code: string): Scope | null {
  let best: Scope | null = null;
  for (const grant of grants) {
    const parsed = parseGrant(grant);
    if (!parsed || parsed.code !== code) continue;
    if (best === null || scopeRank(parsed.scope) > scopeRank(best)) best = parsed.scope;
  }
  return best;
}
