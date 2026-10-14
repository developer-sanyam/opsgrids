import { Suspense, lazy, useEffect, useMemo, useRef, useState } from "react";
import type { ComponentType, ReactNode, SVGProps } from "react";
import { Outlet, useLocation, useNavigate } from "react-router-dom";

import {
  ChevronRightIcon,
  CloseIcon,
  CommandIcon,
  GridIcon,
  HomeIcon,
  LifeBuoyIcon,
  LogOutIcon,
  MoreHorizontalIcon,
  PlusIcon,
  SearchIcon,
} from "../icons";
import { cn } from "../lib/cn";
import { useFocusTrap } from "../lib/useFocusTrap";
import { Avatar } from "./Avatar";
import { BottomNavigation } from "./BottomNavigation";
import type { BottomNavItem } from "./BottomNavigation";
import type { CommandMenuItem, CommandMenuResults } from "./CommandMenu";
import { Drawer } from "./Drawer";
import { Menu } from "./Menu";
import { MobileHeader } from "./MobileHeader";
import { Sidebar } from "./Sidebar";
import type { ModuleLink, NavEntry, NavGroup } from "./Sidebar";

const CommandMenu = lazy(() => import("./CommandMenu").then((m) => ({ default: m.CommandMenu })));

/** A display preference, never anything sensitive — same reasoning as the theme toggle's own key. */
const DESKTOP_COLLAPSE_KEY = "opsgrids.shell.sidebar-collapsed";

/** The bottom bar is for top-level destinations only, and five is what a thumb can tell apart. */
const MAX_BOTTOM_ITEMS = 5;

type IconType = ComponentType<SVGProps<SVGSVGElement>>;

export interface AppShellProduct {
  name: string;
  /** The product's mark — an `<img>`, an inline SVG, whatever the app's `LogoMark` renders. Shown
   *  in the expanded sidebar and the mobile header, where there is room beside it for something
   *  like an environment badge. */
  mark: ReactNode;
  /** The mark alone, for the 72px tablet rail, which has no room for anything beside it —
   *  environment or status badges belong in the expanded sidebar or the page, not here. Defaults
   *  to `mark`, which is already just the mark for every app that doesn't pass one of its own. */
  compactMark?: ReactNode | undefined;
}

/** Display only — never an authorization decision (`frontend/CLAUDE.md`). */
export interface AppShellUser {
  name: string;
  email: string;
  roleLabel?: string | null | undefined;
}

export interface QuickAction {
  key: string;
  label: string;
  icon: IconType;
  onSelect: () => void;
}

export interface CommandMenuConfig {
  /** Both the header trigger's visible text and the palette's input placeholder. */
  placeholder: string;
  /**
   * The palette's last resort — "Search deals for “acme”" navigating to this app's own `?q=` list
   * route, the pattern every sidebar search used before this palette existed. Omit it and the
   * palette is navigation-only, once nothing typed matches a nav item, module or quick action.
   */
  fallback?: { label: string; to: string } | undefined;
  /** Server results for the typed query (an app's `useGlobalSearch`), listed after the static items. */
  results?: CommandMenuResults | undefined;
}

export interface AppShellProps {
  product: AppShellProduct;
  /** This app's own routes — unchanged from the product's own navigation model. */
  nav: NavGroup[];
  /** A product's own Settings or Help, rendered under `nav` without a group title. */
  pinned?: NavEntry[] | undefined;
  /** Cross-subdomain links to other products this caller can open (`usePlatformModules`). Omit
   *  (or pass `[]`) for a surface with no tenant modules — the staff console. */
  modules?: ModuleLink[] | undefined;
  /** Users / Settings / Audit Logs (`useAdminLinks`), already filtered to what the caller holds. */
  adminLinks?: ModuleLink[] | undefined;
  user: AppShellUser;
  onSignOut: () => void;
  /** The header's search trigger and ⌘K command menu. Omitted hides both — no dead search box. */
  search?: CommandMenuConfig | undefined;
  /** Renders nothing when omitted: there is no per-user notification feed yet (the report's API
   *  gaps), and a bell with no feed behind it is worse than no bell. */
  notifications?: ReactNode | undefined;
  helpHref?: string | undefined;
  /** The mobile "+" sheet's contents — already permission-filtered by the app. Omitted (or `[]`)
   *  hides the raised button; a sheet with nothing in it is not a control. */
  quickActions?: QuickAction[] | undefined;
  /**
   * The bottom nav's own app-specific destinations, between Modules/the "+" and More — "Products"
   * for the ecosystem home, "Accounts" and "Subscriptions" for the staff console. Omitted, the bar
   * takes the first three entries of `nav` after Home; pass `[]` for a bar of Home and More only.
   * Whatever the source, the bar holds five at most and More is always the last.
   */
  bottomNavExtra?: NavEntry[] | undefined;
  /** Grants/directory status banners — rendered once, at the top of the routed content, replacing
   *  the near-identical copy eleven apps used to render above the shell. */
  banner?: ReactNode | undefined;
  /** `full-bleed` drops the content column's max width and padding — the storefront theme editor. */
  layout?: "default" | "full-bleed" | undefined;
  badgeTone?: "accent" | "danger" | undefined;
}

/**
 * The account menu: identity plus sign out, built from `Menu` rather than a bespoke popover.
 * `bare` (the mobile header) shows the avatar alone; the desktop/tablet header additionally names
 * the person and trails a chevron — `Menu`'s own label-trigger shape, just fed an avatar as its
 * icon instead of a word.
 */
function AccountMenu({
  user,
  onSignOut,
  bare = false,
}: {
  user: AppShellUser;
  onSignOut: () => void;
  bare?: boolean | undefined;
}) {
  const items = [{ label: "Sign out", icon: <LogOutIcon className="size-5" aria-hidden />, onSelect: onSignOut }];
  if (bare) {
    return (
      <Menu label={`Account menu — ${user.name}`} trigger="icon" icon={<Avatar name={user.name} size="sm" />} align="end" items={items} />
    );
  }
  return (
    <Menu
      label={user.name}
      trigger="label"
      variant="ghost"
      icon={<Avatar name={user.name} size="sm" />}
      align="end"
      items={items}
    />
  );
}

function SearchTrigger({ placeholder, onOpen }: { placeholder: string; onOpen: () => void }) {
  return (
    <button
      type="button"
      onClick={onOpen}
      className="flex h-11 w-full max-w-md items-center gap-2 rounded-full bg-sunken px-4 text-sm text-muted transition-colors hover:bg-ground"
    >
      <SearchIcon className="size-4.5 shrink-0 text-faint" aria-hidden />
      <span className="truncate">{placeholder}</span>
      <kbd className="ml-auto hidden shrink-0 items-center gap-0.5 rounded-chip border border-line bg-surface px-1.5 py-0.5 text-caption font-semibold text-faint sm:inline-flex">
        <CommandIcon className="size-3" />K
      </kbd>
    </button>
  );
}

/** The tablet-and-desktop header: a wide search trigger, the notifications slot, a help link and
 *  the account menu — flush on the page's own ground, no card of its own. Hidden below 768px,
 *  where `MobileHeader` takes over. */
function TopBar({
  search,
  onOpenSearch,
  notifications,
  helpHref,
  profile,
}: {
  search: CommandMenuConfig | undefined;
  onOpenSearch: () => void;
  notifications: ReactNode;
  helpHref: string | undefined;
  profile: ReactNode;
}) {
  return (
    <header className="shell-header-h hidden shrink-0 items-center gap-3 px-4 sm:px-6 md:flex">
      <div className="min-w-0 flex-1">
        {search ? <SearchTrigger placeholder={search.placeholder} onOpen={onOpenSearch} /> : null}
      </div>
      <div className="flex shrink-0 items-center gap-1">
        {notifications}
        {helpHref ? (
          <a
            href={helpHref}
            aria-label="Help"
            className="grid size-10 place-items-center rounded-full text-ink-soft transition-colors hover:bg-ground"
          >
            <LifeBuoyIcon className="size-5" />
          </a>
        ) : null}
        {profile}
      </div>
    </header>
  );
}

/**
 * The shared application shell (v2): a persistent sidebar at 1280px and up, a collapsible icon
 * rail from 768, and a mobile header with a bottom navigation below that — one component, fed
 * entirely by props, so it works the same whether the caller wires it from `@opsgrids/client`'s
 * hooks (every customer product) or from a staff console's own operator auth
 * (`opsgrids-internal`, which has no tenant `modules`).
 *
 * A route change resets scroll and moves focus onto `<main>`; a skip link does the same on demand.
 * The mobile/tablet nav panel and both sheets trap focus while open and hand it back to whatever
 * opened them on close — `useFocusTrap`, the one keyboard contract every overlay here shares.
 */
export function AppShell({
  product,
  nav,
  pinned,
  modules,
  adminLinks,
  user,
  onSignOut,
  search,
  notifications,
  helpHref,
  quickActions,
  bottomNavExtra,
  banner,
  layout = "default",
  badgeTone,
}: AppShellProps) {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const [navDrawerOpen, setNavDrawerOpen] = useState(false);
  const [modulesOpen, setModulesOpen] = useState(false);
  const [quickActionsOpen, setQuickActionsOpen] = useState(false);
  const [commandOpen, setCommandOpen] = useState(false);
  // The desktop (>=1024px) sidebar's own collapse-to-rail, remembered per browser — distinct from
  // the tablet rail below it, which is always collapsed and has no persistent state of its own.
  const [desktopCollapsed, setDesktopCollapsed] = useState(() => {
    try {
      return localStorage.getItem(DESKTOP_COLLAPSE_KEY) === "1";
    } catch {
      return false;
    }
  });
  const drawerRef = useRef<HTMLDivElement>(null);
  const drawerCloseRef = useRef<HTMLButtonElement>(null);
  const mainRef = useRef<HTMLElement>(null);
  const isFirstRender = useRef(true);

  useEffect(() => {
    window.scrollTo({ top: 0 });
    setNavDrawerOpen(false);
    // Skip on mount: the initial screen owns its own focus (the browser's default, or wherever a
    // deep link put it). Only a route *change* should steal focus onto `<main>`.
    if (isFirstRender.current) {
      isFirstRender.current = false;
      return;
    }
    // `preventScroll`: the focus is for the screen reader and the keyboard; letting the browser
    // scroll to <main> would push the sticky header's worth of page off the top on every route.
    mainRef.current?.focus({ preventScroll: true });
  }, [pathname]);

  useEffect(() => {
    try {
      localStorage.setItem(DESKTOP_COLLAPSE_KEY, desktopCollapsed ? "1" : "0");
    } catch {
      // Persistence is best-effort; the current render already reflects the choice.
    }
  }, [desktopCollapsed]);

  useFocusTrap({
    open: navDrawerOpen,
    panelRef: drawerRef,
    onClose: () => setNavDrawerOpen(false),
    initialFocusRef: drawerCloseRef,
    lockBodyScroll: true,
  });

  useEffect(() => {
    if (!search) return;
    function onKey(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setCommandOpen(true);
      }
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [search]);

  const commandItems = useMemo<CommandMenuItem[]>(() => {
    const items: CommandMenuItem[] = [];
    for (const group of nav) {
      for (const item of group.items) {
        items.push({
          key: `nav-${item.to}`,
          label: item.label,
          group: "Go to",
          icon: item.icon,
          onSelect: () => navigate(item.to),
        });
      }
    }
    for (const link of modules ?? []) {
      items.push({
        key: `module-${link.key}`,
        label: link.label,
        group: "Modules",
        ...(link.icon !== undefined ? { icon: link.icon } : {}),
        onSelect: () => {
          window.location.assign(link.href);
        },
      });
    }
    for (const action of quickActions ?? []) {
      items.push({
        key: `action-${action.key}`,
        label: action.label,
        group: "Quick actions",
        icon: action.icon,
        onSelect: action.onSelect,
      });
    }
    return items;
  }, [nav, modules, quickActions, navigate]);

  // A local binding, not a `search.fallback` access, so the narrowing below survives into the
  // closure `onSubmit` captures (property-access narrowing does not; a `const` binding's does).
  const fallbackConfig = search?.fallback;
  const commandFallback = fallbackConfig
    ? {
        label: fallbackConfig.label,
        onSubmit: (query: string) => navigate(`${fallbackConfig.to}?q=${encodeURIComponent(query)}`),
      }
    : undefined;

  const sidebarShared = {
    groups: nav,
    pinned,
    modules,
    adminLinks,
    user,
    productName: product.name,
    ...(badgeTone !== undefined ? { badgeTone } : {}),
  };

  const homeEntry = nav[0]?.items[0];
  const bottomItems: BottomNavItem[] = [];
  if (homeEntry) bottomItems.push({ key: "home", label: "Home", icon: HomeIcon, to: homeEntry.to });
  if (modules && modules.length > 0) {
    bottomItems.push({
      key: "modules",
      label: "Modules",
      icon: GridIcon,
      onSelect: () => setModulesOpen(true),
      expanded: modulesOpen,
    });
  }
  if (quickActions && quickActions.length > 0) {
    bottomItems.push({
      key: "quick-actions",
      label: "New",
      icon: PlusIcon,
      onSelect: () => setQuickActionsOpen(true),
      raised: true,
      expanded: quickActionsOpen,
    });
  }
  // The bar holds five at most, and More is always the last. Destinations the app named come
  // first; without any, the first of its own navigation after Home fill the room that is left.
  const room = Math.max(0, MAX_BOTTOM_ITEMS - bottomItems.length - 1);
  const extras = bottomNavExtra ?? nav.flatMap((group) => group.items).filter((entry) => entry !== homeEntry).slice(0, 3);
  for (const entry of extras.slice(0, room)) {
    bottomItems.push({ key: `extra-${entry.to}`, label: entry.label, icon: entry.icon, to: entry.to });
  }
  bottomItems.push({
    key: "more",
    label: "More",
    icon: MoreHorizontalIcon,
    onSelect: () => setNavDrawerOpen(true),
    expanded: navDrawerOpen,
  });

  return (
    // `overflow-x-clip`: no child, however wide, may stretch the layout viewport and take the fixed
    // bars with it. It clips rather than scrolls, so sticky positioning is untouched.
    <div className="min-h-dvh overflow-x-clip bg-ground">
      <a
        href="#main-content"
        onClick={(event) => {
          // The default fragment jump moves the page's scroll position but not focus in every
          // browser, and never announces anything to a screen reader. Doing it here guarantees
          // both: the skip link's entire job is to hand focus straight to `<main>`.
          event.preventDefault();
          mainRef.current?.focus({ preventScroll: true });
        }}
        className="skip-link rounded-full bg-surface px-4 py-3 text-sm font-bold text-ink shadow-float"
      >
        Skip to main content
      </a>

      <div className="flex min-h-dvh w-full">
        {/* Tablet icon rail, 768–1023px — always collapsed, no persistent state of its own.
            "Expand navigation" opens the temporary nav-drawer overlay (the same one the mobile
            header's menu button and the bottom nav's More button open), rather than growing this
            column in place — there usually isn't enough room at this width to keep it open. Only
            the mark — there is no space beside it for an environment or status badge, so
            `compactMark` (falling back to `mark`) is what shows here rather than whatever richer
            thing the expanded sidebar and mobile header do. */}
        <div className="hidden shell-rail-w shrink-0 border-r border-line bg-surface md:block lg:hidden">
          <div className="shell-side-h sticky top-0 flex flex-col">
            <div className="min-h-0 flex-1">
              <Sidebar {...sidebarShared} logo={product.compactMark ?? product.mark} collapsed />
            </div>
            <button
              type="button"
              onClick={() => setNavDrawerOpen(true)}
              aria-label="Expand navigation"
              aria-expanded={navDrawerOpen}
              className="grid h-12 shrink-0 place-items-center border-t border-line text-ink-soft transition-colors hover:bg-ground"
            >
              <ChevronRightIcon className="size-4" />
            </button>
          </div>
        </div>

        {/* Desktop, 1024px and up — the full sidebar by default (the owner's mockups), flush
            against the viewport edge with a hairline the only separation from the content column.
            Its own collapse control shrinks it to the same rail the tablet width shows, in place
            rather than as an overlay — there is room to keep it open here — and the choice is
            remembered per browser. */}
        <div
          className={cn(
            "hidden shrink-0 border-r border-line bg-surface lg:block",
            desktopCollapsed ? "shell-rail-w" : "shell-sidebar-w",
          )}
        >
          <div className="shell-side-h sticky top-0 flex flex-col">
            <div className="min-h-0 flex-1">
              <Sidebar
                {...sidebarShared}
                logo={desktopCollapsed ? (product.compactMark ?? product.mark) : product.mark}
                collapsed={desktopCollapsed}
              />
            </div>
            <button
              type="button"
              onClick={() => setDesktopCollapsed((collapsed) => !collapsed)}
              aria-label={desktopCollapsed ? "Expand navigation" : "Collapse navigation"}
              aria-expanded={!desktopCollapsed}
              className="grid h-12 shrink-0 place-items-center border-t border-line text-ink-soft transition-colors hover:bg-ground"
            >
              <ChevronRightIcon className={desktopCollapsed ? "size-4" : "size-4 rotate-180"} />
            </button>
          </div>
        </div>

        {/* Main column */}
        <div className="flex min-w-0 flex-1 flex-col">
          <MobileHeader
            logo={product.mark}
            productName={product.name}
            onOpenNav={() => setNavDrawerOpen(true)}
            {...(search ? { search: { placeholder: search.placeholder, onOpen: () => setCommandOpen(true) } } : {})}
            notifications={notifications}
            profile={<AccountMenu user={user} onSignOut={onSignOut} bare />}
          />
          <TopBar
            search={search}
            onOpenSearch={() => setCommandOpen(true)}
            notifications={notifications}
            helpHref={helpHref}
            profile={<AccountMenu user={user} onSignOut={onSignOut} />}
          />

          <main
            id="main-content"
            ref={mainRef}
            tabIndex={-1}
            className={
              layout === "full-bleed"
                ? "min-w-0 max-w-full flex-1 shell-content-bottom-pad focus:outline-none"
                : "mx-auto w-full min-w-0 shell-content-max shell-content-bottom-pad-padded px-3 pt-3 sm:px-6 sm:pt-6 focus:outline-none"
            }
          >
            {banner ? (
              <div className="empty:hidden flex flex-col gap-3 mb-4 empty:mb-0">{banner}</div>
            ) : null}
            <Outlet />
          </main>
        </div>
      </div>

      <BottomNavigation items={bottomItems} />

      {/* The full nav panel: opened by the mobile header's menu button, the bottom nav's More, or
          the tablet rail's expand control. Always the uncollapsed sidebar, modules and admin
          links included. */}
      {navDrawerOpen ? (
        <div
          ref={drawerRef}
          className="fixed inset-0 z-50 lg:hidden"
          role="dialog"
          aria-modal="true"
          aria-label="Navigation"
        >
          <div
            className="absolute inset-0 bg-scrim"
            onClick={() => setNavDrawerOpen(false)}
            aria-hidden
          />
          <div className="shell-drawer-w sheet-safe absolute left-0 top-0 h-full bg-surface shadow-overlay">
            <button
              ref={drawerCloseRef}
              type="button"
              onClick={() => setNavDrawerOpen(false)}
              aria-label="Close navigation"
              className="hit-44 absolute right-3 top-3 z-10 grid size-9 place-items-center rounded-full bg-surface text-ink shadow-card"
            >
              <CloseIcon className="size-5" />
            </button>
            <Sidebar {...sidebarShared} logo={product.mark} onNavigate={() => setNavDrawerOpen(false)} />
          </div>
        </div>
      ) : null}

      <Drawer
        open={modulesOpen}
        onClose={() => setModulesOpen(false)}
        title="Modules"
        size="sm"
      >
        <ul className="flex flex-col gap-1">
          {(modules ?? []).map((link) => {
            const Icon = link.icon;
            return (
              <li key={link.key}>
                <a
                  href={link.href}
                  className="flex h-12 w-full items-center gap-3 rounded-tile px-3 text-sm font-semibold text-ink-soft transition-colors hover:bg-ground"
                >
                  {Icon ? <Icon className="size-5 shrink-0 text-faint" aria-hidden /> : null}
                  {link.label}
                </a>
              </li>
            );
          })}
        </ul>
      </Drawer>

      <Drawer
        open={quickActionsOpen}
        onClose={() => setQuickActionsOpen(false)}
        title="Quick actions"
        size="sm"
      >
        <ul className="flex flex-col gap-1">
          {(quickActions ?? []).map((action) => {
            const Icon = action.icon;
            return (
              <li key={action.key}>
                <button
                  type="button"
                  onClick={() => {
                    setQuickActionsOpen(false);
                    action.onSelect();
                  }}
                  className="flex h-12 w-full items-center gap-3 rounded-tile px-3 text-left text-sm font-semibold text-ink-soft transition-colors hover:bg-ground"
                >
                  <Icon className="size-5 shrink-0 text-faint" aria-hidden />
                  {action.label}
                </button>
              </li>
            );
          })}
        </ul>
      </Drawer>

      {search ? (
        <Suspense fallback={null}>
          <CommandMenu
            open={commandOpen}
            onClose={() => setCommandOpen(false)}
            items={commandItems}
            placeholder={search.placeholder}
            {...(commandFallback ? { fallback: commandFallback } : {})}
            results={search.results}
          />
        </Suspense>
      ) : null}
    </div>
  );
}
