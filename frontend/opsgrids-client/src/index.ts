/**
 * The OpsGrids client SDK: the session, the gateway HTTP layer with RFC 7807 problem parsing, the
 * auth provider / guard every app mounts, and the permission and entitlement hooks that decide
 * what a screen renders. One import, one implementation.
 *
 * **Nothing here is enforcement.** `RequireAuth`, `usePermission`/`RequirePermission` and
 * `useEntitlement` hide UI — a better experience, nothing more. The browser is the caller's
 * machine and a hidden button is a `curl` away; authorization is decided where the query runs
 * (ADR-0003 §3). The gateway refuses the call regardless.
 *
 * `RequirePermission` is not part of this package's public surface (see the comment by its
 * export below) until a real grants source exists.
 */
export { AuthProvider } from "./auth/AuthProvider";
export { AuthContext, useAuth } from "./auth/context";
export type { AuthContextValue, AuthStatus } from "./auth/context";
export { RequireAuth } from "./auth/RequireAuth";
export * as authApi from "./auth/api";
export type {
  LoginComplete,
  LoginMfaRequired,
  LoginOrgSelection,
  LoginResult,
  LoginStep,
  MeOut,
  OrganizationChoice,
} from "./auth/types";

export { decodeAccessToken } from "./auth/token";
export type { AccessClaims } from "./auth/token";

export { API_BASE, ApiError, apiRequest } from "./http";
export type { ProblemJson, RequestOptions } from "./http";

export { PermissionProvider } from "./permissions/PermissionProvider";
// `RequirePermission` is deliberately not exported here: nothing in the repo produces the
// `grants` it reads (the access token carries no `perms` claim, and the gateway's resolved
// grants never reach the browser — see `PermissionProvider`'s doc comment), so every mount
// today would deny every child. A gate that always says no is worse than no gate — the first
// screen to reach for it would silently render its `fallback`. Import it directly from
// `./permissions/RequirePermission` once a real caller has a grants source to hand it, or after
// making `grants` a required prop so an accidental deny-all mount is a type error, not a
// silent one. `useEntitlement` stays exported — it reads the token's real `ent` claim.
export {
  PermissionContext,
  useEntitlement,
  usePermission,
  usePermissions,
  usePermissionStatus,
} from "./permissions/context";
export type { PermissionState, PermissionStatus } from "./permissions/context";
export { hasPermission, parseGrant, scopeFor, scopeRank } from "./permissions/scope";
export type { Scope } from "./permissions/scope";

export {
  configureSession,
  getAccessToken,
  notifyUnauthorized,
  setAccessToken,
  setUnauthorizedHandler,
} from "./session";

export { settle } from "./fixtures";
export { useAsync } from "./useAsync";
export type { AsyncState } from "./useAsync";
