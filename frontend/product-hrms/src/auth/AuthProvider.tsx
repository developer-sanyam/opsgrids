import type { ReactNode } from "react";

import { ProductAuthProvider } from "@opsgrids/client";
import type { MeOut } from "@opsgrids/client";

import { USE_FIXTURES } from "@/api/client";
import { LogoMark } from "@/ui/LogoMark";

/**
 * A stand-in identity for fixture mode, so the whole app renders with no gateway and no login —
 * used for offline design work and screenshots. Never reached live: `USE_FIXTURES` is compiled
 * out of a production build.
 */
const FIXTURE_ORG = {
  organization_id: "org-fixture-0001",
  slug: "opsgrids-india",
  name: "OpsGrids India",
  roles: ["hr_admin"],
};

const FIXTURE_USER: MeOut = {
  user_id: "user-fixture-0001",
  email: "sneha.kulkarni@opsgrids.example.com",
  full_name: "Sneha Kulkarni",
  organization_id: FIXTURE_ORG.organization_id,
  membership_id: "mem-fixture-0001",
  roles: FIXTURE_ORG.roles,
  amr: ["pwd"],
  must_change_password: false,
  mfa_enabled: false,
  organizations: [FIXTURE_ORG],
};

/**
 * The fixture identity's grants (§2.4 gap **G1**): no live source exists yet — the access token
 * carries no `perms` claim, and nothing downstream of it resolves one (`PermissionProvider`'s own
 * doc comment). Fixture mode is the one place a caller is fed in, so the ORG "for another
 * employee" delegation mode (§4.E) has something to demonstrate; a real build passes nothing,
 * which reads as *unknown* and hides every grant-gated control (`core.approval.delegation.write`
 * at ORG collapses to "not shown") until a real grants source exists — the documented fallback,
 * not a bug.
 */
const FIXTURE_GRANTS = [
  "core.approval.request.read:ORG",
  "core.approval.request.decide:OWN",
  "core.approval.delegation.write:ORG",
  "core.approval.policy.write:ORG",
  "hrms.people.employee.read:ORG",
  "hrms.people.employee.write:ORG",
  "hrms.people.employee.read_sensitive:OWN",
  "hrms.time.attendance.read:ORG",
  "hrms.time.attendance.write:ORG",
  "hrms.time.attendance.request:OWN",
  "hrms.time.shift.read:ORG",
  "hrms.time.shift.write:ORG",
];

/** The session, wired to this product's fixture identity. The logic lives in `@opsgrids/client`. */
export function AuthProvider({ children }: { children: ReactNode }) {
  return (
    <ProductAuthProvider
      fixture={USE_FIXTURES ? { user: FIXTURE_USER, grants: FIXTURE_GRANTS } : null}
      mark={<LogoMark className="size-11 animate-pulse" />}
    >
      {children}
    </ProductAuthProvider>
  );
}
