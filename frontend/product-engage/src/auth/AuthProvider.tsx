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
  slug: "vaayu-logistics",
  name: "Vaayu Logistics",
  roles: ["engage_admin"],
};

const FIXTURE_USER: MeOut = {
  user_id: "user-fixture-0001",
  email: "priya.raghavan@vaayu-logistics.example.com",
  full_name: "Priya Raghavan",
  organization_id: FIXTURE_ORG.organization_id,
  membership_id: "mem-fixture-0001",
  roles: FIXTURE_ORG.roles,
  amr: ["pwd"],
  must_change_password: false,
  mfa_enabled: false,
  organizations: [FIXTURE_ORG],
};

/**
 * The fixture identity's grants: an org admin holding every write this product defines, so
 * fixture mode demonstrates the whole CRUD surface offline. A real build passes nothing —
 * `PermissionProvider` then fetches `GET /members/me/grants` itself.
 */
const FIXTURE_GRANTS = [
  "engage.audience.contact.read:ORG",
  "engage.audience.contact.write:ORG",
  "engage.audience.list.read:ORG",
  "engage.audience.list.write:ORG",
  "engage.audience.suppression.read:ORG",
  "engage.audience.suppression.write:ORG",
  "engage.campaign.campaign.read:ORG",
  "engage.campaign.campaign.write:ORG",
  "engage.campaign.campaign.send:ORG",
  "engage.setup.connection.read:ORG",
  "engage.setup.connection.write:ORG",
  "engage.setup.domain.read:ORG",
  "engage.setup.domain.write:ORG",
  "engage.setup.sender.read:ORG",
  "engage.setup.sender.write:ORG",
  "engage.setup.whatsapp.read:ORG",
  "engage.setup.whatsapp.write:ORG",
  "engage.email.template.read:ORG",
  "engage.email.template.write:ORG",
  "engage.inbox.conversation.read:ORG",
  "engage.inbox.conversation.write:ORG",
  "engage.inbox.message.reply:ORG",
  "engage.whatsapp.template.read:ORG",
  "engage.whatsapp.template.write:ORG",
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
