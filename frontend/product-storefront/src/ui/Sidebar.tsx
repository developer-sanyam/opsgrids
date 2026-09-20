import { useAuth } from "@opsgrids/client";
import {
  BoxIcon,
  GlobeIcon,
  LayoutIcon,
  LifeBuoyIcon,
  MonitorIcon,
  SettingsIcon,
  Sidebar as SidebarShell,
  TruckIcon,
} from "@opsgrids/ui";
import type { NavEntry, NavGroup } from "@opsgrids/ui";
import { CONSOLE_STORE_BASE, LIVE_BASE } from "@/routes/live/liveRoutes";
import { LogoMark } from "./LogoMark";

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
 * **Each is one entry, and the store's pages are not listed here.** They used to be: three links,
 * `/store`, `/store/catalogue` and `/store/product`, which were the three pages every theme
 * shipped. A merchant can now add pages, so that list was a constant claiming to be a store — it
 * could not grow a thirty-sixth page, it could not lose a deleted one, and it named three pages a
 * merchant may have set to "Not in the navigation". A navigation that lies about what the store
 * contains is worse than one that does not try.
 *
 * Nothing replaces it here, because nothing here can: the sidebar renders before any store
 * document is fetched, and putting a request behind the navigation would give the app's frame a
 * loading state and an error state of its own. The page list belongs where the document already
 * is — the preview screen's own switcher, built from `store.pages`, which reaches every page
 * including the ones kept out of the buyer's navigation, and the store's own nav bar inside the
 * frame. Both are the document; neither can drift from it.
 */
const GROUPS: NavGroup[] = [
  {
    title: "Store",
    items: [
      { to: "/", label: "Themes", icon: LayoutIcon, end: true },
      { to: "/editor", label: "Editor", icon: BoxIcon },
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
      // "Open the console preview" is the full name and truncates in the rail; the group heading
      // above already says which kind of looking this is, so the item says the act and fits.
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
      product={{ name: "OpsGrids Storefront", tagline: "Themed B2B store" }}
      search={{
        label: "Search themes",
        placeholder: "Search themes",
        to: "/",
      }}
      {...(onNavigate ? { onNavigate } : {})}
    />
  );
}
