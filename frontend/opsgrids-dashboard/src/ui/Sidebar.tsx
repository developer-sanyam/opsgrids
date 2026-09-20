import { useAuth } from "@opsgrids/client";
import {
  BoxIcon,
  LifeBuoyIcon,
  SettingsIcon,
  Sidebar as SidebarShell,
} from "@opsgrids/ui";
import type { NavEntry, NavGroup } from "@opsgrids/ui";

import { LogoMark } from "./LogoMark";

/**
 * The dashboard's navigation. The shell — account, active pill, workspace card, theme toggle —
 * is `@opsgrids/ui`'s `Sidebar`; below is only what is this app's: three destinations and the
 * wording. "Products" is the marketplace and the home page, because a customer who has just
 * signed up has one job here — start something.
 */
const GROUPS: NavGroup[] = [
  {
    title: "Your OpsGrids",
    items: [
      { to: "/", label: "Products", icon: BoxIcon, end: true },
      { to: "/subscriptions", label: "Subscriptions", icon: SettingsIcon },
      { to: "/invoices", label: "Invoices", icon: LifeBuoyIcon },
    ],
  },
];

const PINNED: NavEntry[] = [{ to: "/help", label: "Help", icon: LifeBuoyIcon }];

export function Sidebar({ onNavigate }: { onNavigate?: () => void }) {
  const { user, logout } = useAuth();
  const name = user?.full_name ?? "Account";
  const org =
    user?.organizations.find((o) => o.organization_id === user.organization_id) ??
    user?.organizations[0] ??
    null;

  return (
    <SidebarShell
      groups={GROUPS}
      pinned={PINNED}
      account={{
        name,
        email: user?.email ?? "",
        ...(org ? { organizationName: org.name } : {}),
      }}
      onSignOut={() => void logout()}
      logo={<LogoMark className="size-8 shrink-0" />}
      product={{ name: "OpsGrids", tagline: "One account, every product" }}
      search={{ label: "Search products", placeholder: "Search products", to: "/" }}
      {...(onNavigate ? { onNavigate } : {})}
    />
  );
}
