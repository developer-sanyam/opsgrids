import { useCallback } from "react";

import { getCampaigns } from "@/api/client";
import { useAsync } from "@opsgrids/client";
import {
  HomeIcon,
  LifeBuoyIcon,
  MegaphoneIcon,
  PlugIcon,
  SettingsIcon,
  Share2Icon,
  SparkIcon,
  UsersIcon,
} from "@opsgrids/ui";
import type { NavEntry, NavGroup } from "@opsgrids/ui";

const PINNED: NavEntry[] = [
  { to: "/settings", label: "Settings", icon: SettingsIcon },
  { to: "/help", label: "Help & Getting Started", icon: LifeBuoyIcon },
];

/**
 * This product's navigation, for `AppShell`'s `nav`/`pinned` props.
 *
 * The Campaigns count is real (`total` from the same paged envelope the Campaigns screen reads),
 * not a literal `"14"` that never moves. Reports is not listed: the screen is a placeholder, and a
 * nav entry that opens one is a promise the product cannot keep yet. `/reports` still answers
 * (with the placeholder) for anyone who has the link. It is simply omitted while the count is loading or
 * failed to load, rather than showing a stale or fabricated number.
 */
export function useNavGroups(): { groups: NavGroup[]; pinned: NavEntry[] } {
  const campaignsTotal = useAsync(useCallback(() => getCampaigns({ size: 1 }), []), []);
  const campaignsCount =
    campaignsTotal.status === "success" ? String(campaignsTotal.data.total) : undefined;

  const groups: NavGroup[] = [
    {
      title: "Marketing",
      items: [
        { to: "/", label: "Overview", icon: HomeIcon, end: true },
        {
          to: "/campaigns",
          label: "Campaigns",
          icon: MegaphoneIcon,
          ...(campaignsCount !== undefined ? { count: campaignsCount } : {}),
        },
        { to: "/audiences", label: "Audiences", icon: UsersIcon },
        { to: "/channels", label: "Channels", icon: Share2Icon },
      ],
    },
    {
      title: "Insights",
      items: [
        { to: "/automations", label: "Automations", icon: SparkIcon },
        { to: "/integrations", label: "Integrations", icon: PlugIcon },
      ],
    },
  ];

  return { groups, pinned: PINNED };
}
