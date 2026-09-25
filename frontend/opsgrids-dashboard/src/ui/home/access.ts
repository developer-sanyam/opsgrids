/**
 * The gate every Home widget resolves before it renders anything: entitlement (the token's `ent`
 * claim) first, then the permission its data actually needs — never the other way round, and
 * never both at once, so the copy always names the right reason (ADR-0003 §3; `docs/design`
 * dashboard audit §5.0.6).
 *
 * A widget calls this once its `usePermissions()` status is `"ready"`; while it is `"loading"` (or
 * `"error"`, which the app shell's own banner already explains) the caller renders a skeleton
 * instead — never a flash of "no access" that a moment later becomes real data.
 */
export type AccessState = "plan_gated" | "no_access" | "ready";

export function resolveAccess(entitled: boolean, permitted: boolean): AccessState {
  if (!entitled) return "plan_gated";
  if (!permitted) return "no_access";
  return "ready";
}
