/**
 * Auth wire types — a field-for-field mirror of `services/auth/app/schemas.py`. The login
 * endpoint returns a discriminated union on `status`; `select-org` and `me` return the shapes
 * below. Kept apart from every product's data types so the auth slice stands on its own.
 */

/** One organization a user can act in, with the roles they hold there. */
export interface OrganizationChoice {
  organization_id: string;
  slug: string;
  name: string;
  roles: string[];
  /** Index-aligned with `roles` — display labels from the `roles` table, falling back to
   * the code itself for one the server has no name for. Optional in the type only so a
   * fixture identity (offline/demo mode) built before this field existed still type-checks;
   * the real server always sends it. `useCurrentUser` treats an absent entry as a missing
   * name, same as an empty one. */
  role_names?: string[];
}

/** `TokenOut` — a completed sign-in. The refresh token is an httpOnly cookie, not in the body. */
export interface LoginComplete {
  status: "complete";
  access_token: string;
  token_type: "Bearer";
  expires_in: number;
  organization_id: string;
  membership_id: string;
}

/** `OrgSelectionRequired` — the user belongs to several orgs and must pick one. */
export interface LoginOrgSelection {
  status: "org_selection_required";
  pre_auth_token: string;
  organizations: OrganizationChoice[];
}

/** `MfaRequired` — a second factor is needed (not yet handled by these product slices). */
export interface LoginMfaRequired {
  status: "mfa_required";
  pre_auth_token: string;
}

export type LoginResult = LoginComplete | LoginOrgSelection | LoginMfaRequired;

/** `MeOut` — who the caller is, plus every org they can switch to. */
export interface MeOut {
  user_id: string;
  email: string;
  full_name: string;
  organization_id: string | null;
  membership_id: string | null;
  roles: string[];
  /** Index-aligned with `roles`, same source and fallback as `OrganizationChoice.role_names`. */
  role_names?: string[];
  amr: string[];
  must_change_password: boolean;
  mfa_enabled: boolean;
  organizations: OrganizationChoice[];
}

/** What `login()` reports back to the screen, so it knows whether to show the org picker. */
export type LoginStep =
  | { kind: "complete" }
  | { kind: "org_selection"; organizations: OrganizationChoice[] }
  | { kind: "mfa_required" };
