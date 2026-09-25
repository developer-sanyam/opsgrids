import { AppShell as Shell } from "@opsgrids/ui";

import { useAuth } from "@/auth/context";
import { EnvBadge, useNavGroups } from "./ui";
import { LogoMark } from "./ui/icons";

const ROLE_LABEL: Record<string, string> = {
  owner: "Owner",
  billing_ops: "Billing Ops",
  support: "Support",
};

/**
 * The console shell is `@opsgrids/ui`'s shared `AppShell` — the same sidebar, rail, mobile header
 * and bottom navigation every customer product uses, wired to this console's own operator auth
 * (`@/auth/context`) rather than `@opsgrids/client`'s tenant hooks. No `modules` (a customer's
 * products are not this console's concern) and no `adminLinks` (this console's own Governance
 * group in `useNavGroups` covers what that prop is for elsewhere).
 */
export function AppShell() {
  const { operator, logout } = useAuth();
  const { groups } = useNavGroups();

  const name = operator?.display_name ?? "Operator";
  const roles = operator?.operator_roles ?? [];
  const roleLabel = roles.map((role) => ROLE_LABEL[role] ?? role).join(" · ") || null;

  return (
    <Shell
      product={{
        name: "OpsGrids Internal",
        mark: (
          <span className="flex items-center gap-2">
            <LogoMark className="size-7" />
            <EnvBadge />
          </span>
        ),
        // The 72px tablet rail has no room beside the mark for the environment badge — just the
        // mark there, unclipped; the expanded sidebar and mobile header keep the full version.
        compactMark: <LogoMark className="size-7" />,
      }}
      nav={groups}
      user={{ name, email: operator?.email ?? "", roleLabel }}
      onSignOut={() => void logout()}
      search={{ placeholder: "Search accounts", fallback: { label: "Search accounts for", to: "/accounts" } }}
    />
  );
}
