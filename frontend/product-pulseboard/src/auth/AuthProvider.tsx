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
  slug: "opsgrids-studio",
  name: "OpsGrids Studio",
  roles: ["delivery_lead"],
};

const FIXTURE_USER: MeOut = {
  user_id: "user-fixture-0001",
  email: "nadia.rahman@opsgrids.example.com",
  full_name: "Nadia Rahman",
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
  "pulseboard.board.task.read:ORG",
  "pulseboard.board.task.write:ORG",
  "pulseboard.engagements.project.read:ORG",
  "pulseboard.engagements.project.write:ORG",
  "pulseboard.standup.entry.read:ORG",
  "pulseboard.standup.entry.write:ORG",
  "pulseboard.team.person.read:ORG",
  "pulseboard.team.person.write:ORG",
  "pulseboard.time.timesheet.read:ORG",
  "pulseboard.time.timesheet.write:ORG",
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
