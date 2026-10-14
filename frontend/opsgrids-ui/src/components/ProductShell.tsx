import { useMemo } from "react";
import type { ComponentType, ReactNode, SVGProps } from "react";
import { useNavigate } from "react-router-dom";

import { moduleIcon } from "../lib/moduleIcons";
import { AppShell } from "./AppShell";
import type { AppShellProduct, AppShellUser } from "./AppShell";
import { Button } from "./Button";
import { InlineAlert } from "./InlineAlert";
import type { ModuleLink, NavEntry, NavGroup } from "./Sidebar";

type IconType = ComponentType<SVGProps<SVGSVGElement>>;

/** A cross-product module as the platform hooks hand it over: an icon *key*, resolved here. */
export interface ProductShellModule {
  key: string;
  label: string;
  href: string;
  iconKey: string;
}

/** A shortcut in the mobile "+" sheet and the command menu: where it goes, not what it runs. */
export interface ProductQuickAction {
  key: string;
  label: string;
  icon: IconType;
  /** The route it opens — the create screen, or the list the action starts from. */
  to: string;
}

export interface ProductBannerCopy {
  /** The warning's sentence. */
  title: string;
  /** What it means for the screen, when the title alone does not say. */
  body?: string | undefined;
}

interface ProductShellProps {
  product: AppShellProduct;
  nav: NavGroup[];
  pinned?: NavEntry[] | undefined;
  /** Cross-product modules (`useProductShell().modules`). */
  modules: readonly ProductShellModule[];
  /** Admin destinations, already absolute (`useProductShell().adminLinks`). */
  adminLinks: readonly ModuleLink[];
  /** `null` until `/auth/me` has answered; the shell renders nothing meanwhile. */
  user: AppShellUser | null;
  onSignOut: () => void;
  /** The header search trigger's text and the palette's input placeholder. */
  searchPlaceholder: string;
  /** The palette's last resort when nothing typed matches: "Search deals for “acme”" opens `to?q=`. */
  searchFallback: { label: string; to: string };
  notifications?: ReactNode | undefined;
  /**
   * The grants fetch's state (`useProductShell().grants`). When it failed, `<Can>` and `useCan`
   * fail closed and every write control vanishes with no explanation — this banner is the
   * explanation, with a retry. Omit it (or `bannerCopy`) for an app that gates screens one by one.
   */
  grants?: { status: string; reload: () => void } | undefined;
  /** The words of the permissions banner, per product. */
  bannerCopy?: ProductBannerCopy | undefined;
  /** Banners beside the permissions one, e.g. a product's own directory-health warning. */
  extraBanners?: ReactNode | undefined;
  /**
   * Routes of the bottom nav's own destinations, between Modules/"+" and More. Each is looked up in
   * `nav` and `pinned`, so the label and icon are the sidebar's own; one the caller cannot see (the
   * nav omits it) is skipped. The bar holds five at most — Home and More take two.
   */
  bottomNavExtra?: readonly string[] | undefined;
  quickActions?: readonly ProductQuickAction[] | undefined;
}

/**
 * The shell every customer product wraps its routes in: `AppShell` with the arrangement all of them
 * repeated — modules and admin links from the platform hooks, the permissions-health banner, the
 * bottom-nav destinations and the mobile "+" sheet. A product supplies its mark, its nav and its
 * words; the wiring is here once.
 *
 * Presentational: it takes the data `useProductShell()` (`@opsgrids/client`) reads, so the design
 * system stays free of auth and HTTP.
 */
export function ProductShell({
  product,
  nav,
  pinned,
  modules,
  adminLinks,
  user,
  onSignOut,
  searchPlaceholder,
  searchFallback,
  notifications,
  grants,
  bannerCopy,
  extraBanners,
  bottomNavExtra,
  quickActions,
}: ProductShellProps) {
  const navigate = useNavigate();

  const moduleLinks = useMemo<ModuleLink[]>(
    () => modules.map((m) => ({ key: m.key, label: m.label, href: m.href, icon: moduleIcon(m.iconKey) })),
    [modules],
  );

  const bottomEntries = useMemo<NavEntry[] | undefined>(() => {
    if (bottomNavExtra === undefined) return undefined;
    const known = [...nav.flatMap((group) => group.items), ...(pinned ?? [])];
    return bottomNavExtra.flatMap((to) => known.filter((entry) => entry.to === to).slice(0, 1));
  }, [bottomNavExtra, nav, pinned]);

  const actions = useMemo(
    () =>
      quickActions?.map((action) => ({
        key: action.key,
        label: action.label,
        icon: action.icon,
        onSelect: () => navigate(action.to),
      })),
    [quickActions, navigate],
  );

  if (user === null) return null;

  const permissionsFailed = grants !== undefined && bannerCopy !== undefined && grants.status === "error";

  return (
    <AppShell
      product={product}
      nav={nav}
      pinned={pinned}
      modules={moduleLinks}
      adminLinks={[...adminLinks]}
      user={user}
      onSignOut={onSignOut}
      search={{ placeholder: searchPlaceholder, fallback: searchFallback }}
      notifications={notifications}
      {...(bottomEntries !== undefined ? { bottomNavExtra: bottomEntries } : {})}
      {...(actions !== undefined ? { quickActions: actions } : {})}
      banner={
        <>
          {permissionsFailed ? <GrantsStatusBanner copy={bannerCopy} onRetry={grants.reload} /> : null}
          {extraBanners}
        </>
      }
    />
  );
}

/**
 * Permissions could not be checked. Shown once, at the shell, never per screen: a screen with every
 * write control missing and no reason is indistinguishable from a narrow role.
 */
export function GrantsStatusBanner({ copy, onRetry }: { copy: ProductBannerCopy; onRetry: () => void }) {
  return (
    <InlineAlert
      tone="warning"
      title={copy.title}
      action={
        <Button size="sm" variant="secondary" onClick={onRetry}>
          Try again
        </Button>
      }
    >
      {copy.body}
    </InlineAlert>
  );
}
