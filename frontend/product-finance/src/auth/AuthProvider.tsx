import type { ReactNode } from "react";

import { AuthProvider as BaseAuthProvider } from "@opsgrids/client";
import type { MeOut } from "@opsgrids/client";

import { USE_FIXTURES } from "@/api/client";

/**
 * A stand-in identity for fixture mode, so the whole app renders with no gateway and no login —
 * used for offline design work and screenshots. Never reached live: `USE_FIXTURES` is compiled
 * out of a production build.
 */
const FIXTURE_ORG = {
  organization_id: "org-fixture-0001",
  slug: "opsgrids-india",
  name: "OpsGrids India",
  roles: ["finance_admin"],
};

const FIXTURE_USER: MeOut = {
  user_id: "user-fixture-0001",
  email: "rahul.deshpande@opsgrids.example.com",
  full_name: "Rahul Deshpande",
  organization_id: FIXTURE_ORG.organization_id,
  membership_id: "mem-fixture-0001",
  roles: FIXTURE_ORG.roles,
  amr: ["pwd"],
  must_change_password: false,
  mfa_enabled: false,
  organizations: [FIXTURE_ORG],
};

/** The session, wired to this product's fixture identity. The logic lives in `@opsgrids/client`. */
export function AuthProvider({ children }: { children: ReactNode }) {
  return (
    <BaseAuthProvider fixtureUser={USE_FIXTURES ? FIXTURE_USER : null}>
      {children}
    </BaseAuthProvider>
  );
}
