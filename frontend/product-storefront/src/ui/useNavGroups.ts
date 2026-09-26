import {
  BoxIcon,
  GlobeIcon,
  LayoutIcon,
  MailIcon,
  MonitorIcon,
  PackageIcon,
  SettingsIcon,
  TagIcon,
  TruckIcon,
} from "@opsgrids/ui";
import type { NavEntry, NavGroup } from "@opsgrids/ui";
import { CONSOLE_STORE_BASE, LIVE_BASE } from "@/routes/live/liveRoutes";

/**
 * The two ways to look at the same store, named apart on purpose.
 *
 * **Console preview** (`/store`) is the merchant checking their work: the sidebar is still there,
 * a page header explains what they are looking at, and a device toggle fakes a width. **Live
 * store** (`/live`) is the page a buyer gets — full viewport, no console furniture, the store's
 * own header and footer as the only chrome.
 *
 * Calling both of them "Preview" would hide the only difference that matters, so the groups say
 * which is which and each entry names its destination rather than the act of previewing.
 *
 * **Each is one entry, and the store's pages are not listed here.** A merchant can add pages, so a
 * fixed list here would be a navigation that cannot grow one, cannot lose a deleted one, and names
 * pages a merchant may have set to "Not in the navigation". The page list belongs where the
 * document already is — the preview screen's own switcher, and the store's own nav bar inside the
 * frame. Both are the document; neither can drift from it.
 */
const GROUPS: NavGroup[] = [
  {
    title: "Store",
    items: [
      { to: "/", label: "Themes", icon: LayoutIcon, end: true },
      { to: "/editor", label: "Editor", icon: BoxIcon },
      { to: "/products", label: "Products", icon: PackageIcon },
      { to: "/categories", label: "Categories", icon: TagIcon },
      { to: "/enquiries", label: "Enquiries", icon: MailIcon },
      // The delivery admin shipped reachable by URL only, which is the same as not shipping it:
      // zones, the postal-code map and the demand report are a merchant's screen, not a deep link
      // somebody hands them. The label matches the screen's own `PageHeader` title.
      { to: "/delivery", label: "Delivery", icon: TruckIcon },
    ],
  },
  {
    title: "Console preview",
    items: [
      // No `end`: every store page is previewed under this base, and all of them are "the console
      // preview". The entry opens on the home page; the switcher on the screen moves between them.
      { to: CONSOLE_STORE_BASE, label: "Open the preview", icon: MonitorIcon },
    ],
  },
  {
    title: "Live store",
    items: [
      // No `end`: the buyer's pages hang off this base, and all of them are "the live store".
      { to: LIVE_BASE, label: "Open the live store", icon: GlobeIcon },
    ],
  },
];

// `/help` is not pinned here: there is no screen behind it yet, and frontend/CLAUDE.md's rule
// against placeholder code applies to navigation as much as to components. Add it back the day a
// help screen ships.
const PINNED: NavEntry[] = [{ to: "/settings", label: "Settings", icon: SettingsIcon }];

/** This product's navigation, for `AppShell`'s `nav`/`pinned` props. */
export function useNavGroups(): { groups: NavGroup[]; pinned: NavEntry[] } {
  return { groups: GROUPS, pinned: PINNED };
}
