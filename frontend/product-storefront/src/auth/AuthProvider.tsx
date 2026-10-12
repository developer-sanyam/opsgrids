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
  roles: ["storefront_admin"],
};

const FIXTURE_USER: MeOut = {
  user_id: "user-fixture-0001",
  email: "harpreet.kaur@opsgrids.example.com",
  full_name: "Harpreet Kaur",
  organization_id: FIXTURE_ORG.organization_id,
  membership_id: "mem-fixture-0001",
  roles: FIXTURE_ORG.roles,
  amr: ["pwd"],
  must_change_password: false,
  mfa_enabled: false,
  organizations: [FIXTURE_ORG],
};

/**
 * The fixture identity's grants: a storefront admin holding every write this product defines, so
 * fixture mode demonstrates the whole CRUD surface offline. A real build passes nothing —
 * `PermissionProvider` then fetches `GET /members/me/grants` itself.
 */
const FIXTURE_GRANTS = [
  "storefront.store.settings.read:ORG",
  "storefront.store.settings.write:ORG",
  "storefront.delivery.zone.read:ORG",
  "storefront.delivery.zone.write:ORG",
  "storefront.catalogue.product.read:ORG",
  "storefront.catalogue.product.write:ORG",
  "storefront.sales.enquiry.read:ORG",
  "storefront.sales.enquiry.write:ORG",
  "storefront.sales.enquiry.delete:ORG",
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
