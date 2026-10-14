/**
 * The OpsGrids client SDK: the session, the gateway HTTP layer with RFC 7807 problem parsing, the
 * auth provider / guard every app mounts, and the permission and entitlement hooks that decide
 * what a screen renders. One import, one implementation.
 *
 * **Nothing here is enforcement.** `RequireAuth`, `useCan`/`<Can>`/`<RequirePermission>` and
 * `useEntitlement` hide UI — a better experience, nothing more. The browser is the caller's
 * machine and a hidden button is a `curl` away; authorization is decided where the query runs
 * (ADR-0003 §3). The gateway refuses the call regardless.
 *
 * `PermissionProvider` reads the caller's grants from `GET /members/me/grants` once the session
 * is authenticated, and again whenever the organization, the membership or the token's `pv`
 * changes. `useCan` / `<Can>` gate a control on them; `<RequirePermission>` gates a screen.
 */
export { AuthProvider } from "./auth/AuthProvider";
export { AuthContext, useAuth } from "./auth/context";
export type { AuthContextValue, AuthStatus } from "./auth/context";
export { RequireAuth } from "./auth/RequireAuth";
export { ProductAuthProvider } from "./auth/ProductAuthProvider";
export { DEMO_PASSWORD, useProductLogin } from "./auth/useProductLogin";
export type { ProductLogin } from "./auth/useProductLogin";
export type { ProductFixture } from "./auth/ProductAuthProvider";
export { RedirectToCentralLogin } from "./auth/RedirectToCentralLogin";
export type { RedirectToCentralLoginProps } from "./auth/RedirectToCentralLogin";
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

// Member step-up: a fresh one-time code for one dangerous call (reveal, unmask). `StepUpDialog` in
// `@opsgrids/ui` is the prompt; `useStepUp().dialogProps` feeds it.
export { StepUpError } from "./auth/stepUpError";
export type { StepUpFailure } from "./auth/stepUpError";
export type { StepUpToken } from "./auth/types";
export {
  DEFAULT_REVEAL_VISIBLE_SECONDS,
  StepUpCancelledError,
  clearStepUpToken,
  isStepUpCancelled,
  revealRequest,
  useMfaEnrolled,
  useRevealedField,
  useStepUp,
  withStepUp,
} from "./stepup";
export type {
  RevealedField,
  RevealedFieldOptions,
  RevealHeaders,
  RevealNotice,
  RevealRequestOptions,
  RevealSubject,
  StepUpMfaState,
  StepUpPhase,
  StepUpPromptError,
  StepUpPromptProps,
  StepUpRunOptions,
  UseStepUpResult,
} from "./stepup";

export { decodeAccessToken } from "./auth/token";
export type { AccessClaims } from "./auth/token";

export { configureAuthOrigin, getAuthOrigin } from "./auth/origin";
export { refreshAccessToken } from "./auth/refresh";
export type { RefreshResult } from "./auth/refresh";

export { PLATFORM_APP_HOSTS, isAllowedRedirectTarget } from "./sso/platformHosts";

export { csvCell, guardCsvCell } from "./csv";
export { downloadCsv, downloadCsvFile, downloadCsvGrid, toCsv, toCsvGrid } from "./csvExport";
export type { CsvColumn } from "./csvExport";
export { API_BASE, ApiError, apiRequest } from "./http";
export type { ProblemJson, RequestOptions } from "./http";

export { fieldError, fieldErrorMap, fieldErrors, problemMessage, problemSlug } from "./problem";
export type { FieldError } from "./problem";

export { PermissionProvider } from "./permissions/PermissionProvider";
export { fetchMyGrants } from "./permissions/api";
export type { MyGrantsOut } from "./permissions/api";
// `RequirePermission` is exported again: `PermissionProvider` now reads the caller's real grants
// from `GET /members/me/grants`, so a mount is answered rather than denied by default. `<Can>` is
// the one to reach for around a *control*; this one gates a screen, where an explanatory
// fallback is worth rendering.
export { Can } from "./permissions/Can";
export { RequirePermission } from "./permissions/RequirePermission";
export {
  PermissionContext,
  useCan,
  useEntitlement,
  usePermission,
  usePermissions,
  usePermissionStatus,
} from "./permissions/context";
export type { GrantsStatus, PermissionState, PermissionStatus } from "./permissions/context";
export { hasPermission, parseGrant, scopeFor, scopeRank } from "./permissions/scope";
export type { Scope } from "./permissions/scope";

export {
  configureSession,
  getAccessToken,
  notifyUnauthorized,
  setAccessToken,
  setUnauthorizedHandler,
} from "./session";

export {
  configurePlatformHosts,
  getPlatformHost,
  PLATFORM_PRODUCTS,
  resetPlatformHosts,
} from "./platform/catalogue";
export type { PlatformProduct, PlatformProductKey } from "./platform/catalogue";
export { usePlatformModules } from "./platform/usePlatformModules";
export type { PlatformModule } from "./platform/usePlatformModules";
export { useCurrentUser } from "./platform/useCurrentUser";
export { useProductShell } from "./platform/useProductShell";
export type { ProductShellData } from "./platform/useProductShell";
export type { CurrentUser } from "./platform/useCurrentUser";
export { useOrgProfile } from "./platform/useOrgProfile";
export type { OrgProfile } from "./platform/useOrgProfile";
export { useAdminLinks } from "./platform/useAdminLinks";
export type { AdminLink, AdminLinkKey } from "./platform/useAdminLinks";
export { useGlobalSearch } from "./platform/useGlobalSearch";
export type {
  GlobalSearchGroup,
  GlobalSearchResultItem,
  GlobalSearchState,
  GlobalSearchStatus,
  UseGlobalSearchOptions,
} from "./platform/useGlobalSearch";

export {
  fetchMyNotifications,
  markAllNotificationsRead,
  markNotificationRead,
} from "./notifications/api";
export type {
  FetchNotificationsParams,
  MarkAllReadResult,
  NotificationItem,
  NotificationPage,
} from "./notifications/api";
export { useNotifications } from "./notifications/useNotifications";
export type {
  UseNotificationsOptions,
  UseNotificationsResult,
} from "./notifications/useNotifications";
export { NotificationsBell } from "./notifications/NotificationsBell";

export { paginateFixture, settle } from "./fixtures";
export type { FixturePage, PaginateFixtureOptions } from "./fixtures";
export { buildQuery } from "./query";
export type { BuildQueryOptions, QueryValue } from "./query";
export { useAsync } from "./useAsync";
export type { AsyncControls, AsyncState } from "./useAsync";
export { useMutation } from "./useMutation";
export type { Mutation, MutationOptions } from "./useMutation";

export {
  ASSET_ACCEPTED_TYPES,
  ASSET_MAX_UPLOAD_BYTES,
  ASSET_PUBLIC_HELPER,
  uploadAsset,
} from "./documents/assets";
export type { AssetOut, UploadAssetControls } from "./documents/assets";

// Excel import/export (ADR-0022): the engine behind `@opsgrids/ui`'s ImportExportButton.
export {
  CLEAR_TOKEN,
  RowProblem,
  buildUpdate,
  checkImportEntity,
  collectPages,
  defineImportEntity,
  indexExisting,
  isClearToken,
  majorToMinor,
  makeLookup,
  matchRow,
  minorToMajor,
  normaliseKey,
  planUpdate,
  unacceptedLookupValue,
  useImportExport,
  columnOf,
  labelOfOption,
  lookupField,
  numberOf,
  optionOf,
  refuseUnlistedReferences,
  requiredTextOf,
  resolveLookup,
  resolveReference,
  sameLines,
  storedReference,
  storedText,
  textOf,
  upperCased,
} from "./dataexchange";
export type {
  BaselineUse,
  ColumnKind,
  ColumnOption,
  DataExchangeActions,
  DataExchangeMeta,
  DataExchangeState,
  ExportPage,
  ExportProgress,
  ExportSource,
  ExtraStep,
  ImportColumn,
  ImportContext,
  ImportEntity,
  ImportMode,
  ImportPhase,
  ImportPreview,
  ImportProgress,
  ImportSummary,
  Lookup,
  LookupSet,
  ParsedRow,
  ParsedValue,
  PreviewAction,
  PreviewRow,
  RowOutcome,
  RowStatus,
  SheetValue,
  StepRunner,
  UpdateField,
  UpdatePlan,
  UpdateSpec,
  UseImportExportOptions,
  UseImportExportResult,
} from "./dataexchange";
