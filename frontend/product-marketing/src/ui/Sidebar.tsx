import { useAuth } from "@opsgrids/client";
import {
  FileTextIcon,
  HomeIcon,
  LifeBuoyIcon,
  MegaphoneIcon,
  PlugIcon,
  SettingsIcon,
  Share2Icon,
  Sidebar as SidebarShell,
  SparkIcon,
  UsersIcon,
} from "@opsgrids/ui";
import type { NavEntry, NavGroup } from "@opsgrids/ui";
import { LogoMark } from "./LogoMark";

const GROUPS: NavGroup[] = [
  {
    title: "Marketing",
    items: [
      { to: "/", label: "Overview", icon: HomeIcon, end: true },
      { to: "/campaigns", label: "Campaigns", icon: MegaphoneIcon, count: "14" },
      { to: "/audiences", label: "Audiences", icon: UsersIcon },
      { to: "/channels", label: "Channels", icon: Share2Icon },
    ],
  },
  {
    title: "Insights",
    items: [
      { to: "/reports", label: "Reports", icon: FileTextIcon },
      { to: "/automations", label: "Automations", icon: SparkIcon },
      { to: "/integrations", label: "Integrations", icon: PlugIcon },
    ],
  },
];

const PINNED: NavEntry[] = [
  { to: "/settings", label: "Settings", icon: SettingsIcon },
  { to: "/help", label: "Help & Getting Started", icon: LifeBuoyIcon },
];

/**
 * This product's navigation. The shell — account switcher, ⌘K search, active pill, workspace card
 * and theme toggle — is `@opsgrids/ui`'s `Sidebar`. Everything below is the part that is genuinely
 * this product's: its groups, its mark and its wording.
 */
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
      product={{ name: "OpsGrids Marketing", tagline: "Revenue-first growth" }}
      search={{
        label: "Search campaigns",
        placeholder: "Search campaigns",
        to: "/campaigns",
      }}
      {...(onNavigate ? { onNavigate } : {})}
    />
  );
}
