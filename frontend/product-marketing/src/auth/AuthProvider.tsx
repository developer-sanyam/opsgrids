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
  roles: ["marketing_admin"],
};

const FIXTURE_USER: MeOut = {
  user_id: "user-fixture-0001",
  email: "meghna.rao@opsgrids.example.com",
  full_name: "Meghna Rao",
  organization_id: FIXTURE_ORG.organization_id,
  membership_id: "mem-fixture-0001",
  roles: FIXTURE_ORG.roles,
  amr: ["pwd"],
  must_change_password: false,
  mfa_enabled: false,
  organizations: [FIXTURE_ORG],
};

/**
 * The fixture identity's grants: a marketing admin holding both permissions this product defines,
 * so fixture mode demonstrates the whole CRUD surface offline. A real build passes nothing —
 * `PermissionProvider` then fetches `GET /members/me/grants` itself.
 */
const FIXTURE_GRANTS = [
  "marketing.analytics.campaign.read:ORG",
  "marketing.analytics.campaign.write:ORG",
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
