import type { ReactNode } from "react";

import { AuthProvider as BaseAuthProvider, PermissionProvider } from "@opsgrids/client";
import type { MeOut } from "@opsgrids/client";

import { USE_FIXTURES } from "@/api/client";

/**
 * A stand-in identity for fixture mode, so the whole app renders with no gateway and no login —
 * used for offline design work and screenshots. Never reached live: `USE_FIXTURES` is compiled
 * out of a production build.
 */
const FIXTURE_ORG = {
  organization_id: "org-fixture-0001",
  slug: "opsgrids-studio",
  name: "OpsGrids Delivery Studio",
  roles: ["projects_admin"],
};

const FIXTURE_USER: MeOut = {
  user_id: "user-fixture-0001",
  email: "aarav.sharma@opsgrids.example.com",
  full_name: "Aarav Sharma",
  organization_id: FIXTURE_ORG.organization_id,
  membership_id: "mem-fixture-0001",
  roles: FIXTURE_ORG.roles,
  amr: ["pwd"],
  must_change_password: false,
  mfa_enabled: false,
  organizations: [FIXTURE_ORG],
};

/**
 * The fixture identity's grants — the delivery administrator's full set, so fixture mode
 * demonstrates every gated control. A real build passes nothing: `PermissionProvider` then
 * fetches `GET /members/me/grants` once the session is authenticated.
 */
const FIXTURE_GRANTS = [
  "projects.board.task.read:ORG",
  "projects.board.task.write:ORG",
  "projects.delivery.milestone.read:ORG",
  "projects.delivery.milestone.write:ORG",
  "projects.portfolio.project.read:ORG",
  "projects.portfolio.project.write:ORG",
  "projects.team.member.read:ORG",
  "projects.team.member.write:ORG",
];

/** The session, wired to this product's fixture identity, with the permission provider mounted
 *  inside it (CLAUDE.md: "make sure PermissionProvider is mounted inside the auth provider"). */
export function AuthProvider({ children }: { children: ReactNode }) {
  return (
    <BaseAuthProvider fixtureUser={USE_FIXTURES ? FIXTURE_USER : null}>
      <PermissionProvider grants={USE_FIXTURES ? FIXTURE_GRANTS : null}>
        {children}
      </PermissionProvider>
    </BaseAuthProvider>
  );
}
