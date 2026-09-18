/**
 * The OpsGrids client SDK: the session, the gateway HTTP layer with RFC 7807 problem parsing, and
 * the auth provider / guard every app mounts. One import, one implementation.
 *
 * Nothing here is enforcement — `RequireAuth` and the permission checks that will join it hide
 * UI. The gateway refuses the call regardless.
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

export { API_BASE, ApiError, apiRequest } from "./http";
export type { ProblemJson, RequestOptions } from "./http";

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
