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
  slug: "opsgrids-india",
  name: "OpsGrids India",
  roles: ["support_lead"],
};

const FIXTURE_USER: MeOut = {
  user_id: "user-fixture-0001",
  email: "priya.nair@opsgrids.example.com",
  full_name: "Priya Nair",
  organization_id: FIXTURE_ORG.organization_id,
  membership_id: "mem-fixture-0001",
  roles: FIXTURE_ORG.roles,
  amr: ["pwd"],
  must_change_password: false,
  mfa_enabled: false,
  organizations: [FIXTURE_ORG],
};

/**
 * The fixture identity's grants: the full `SUPPORT_ADMIN` set, so offline design work can see
 * every write control this app has (reply, assign, status, create/edit tickets, agent
 * administration, the SLA policy). A live build never reaches this — `PermissionProvider` fetches
 * the caller's real grants from `GET /members/me/grants` whenever `grants` is not overridden.
 */
const FIXTURE_GRANTS = [
  "support.desk.ticket.read:ORG",
  "support.desk.ticket.write:ORG",
  "support.desk.agent.write:ORG",
  "support.desk.sla_policy.write:ORG",
];

/** The session, wired to this product's fixture identity. The logic lives in `@opsgrids/client`. */
export function AuthProvider({ children }: { children: ReactNode }) {
  return (
    <BaseAuthProvider fixtureUser={USE_FIXTURES ? FIXTURE_USER : null}>
      <PermissionProvider grants={USE_FIXTURES ? FIXTURE_GRANTS : null}>
        {children}
      </PermissionProvider>
    </BaseAuthProvider>
  );
}
