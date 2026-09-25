import type { ComponentType, ReactNode, SVGProps } from "react";
import { NavLink } from "react-router-dom";

import { cn } from "../lib/cn";
import { Avatar } from "./Avatar";

type IconType = ComponentType<SVGProps<SVGSVGElement>>;

export interface NavEntry {
  to: string;
  label: string;
  icon: IconType;
  end?: boolean | undefined;
  /** A count that wants attention — rendered as a filled pill. */
  badge?: string | undefined;
  /** A plain count, rendered quietly. */
  count?: string | undefined;
}

export interface NavGroup {
  title: string;
  items: NavEntry[];
}

/** A cross-product module or an admin destination: a plain link, in or out of this app. */
export interface ModuleLink {
  key: string;
  label: string;
  href: string;
  icon?: IconType | undefined;
}

/** Who is signed in, as the footer identity card needs it — display only (`frontend/CLAUDE.md`:
 *  a UI gate is never enforcement, and neither is this card). */
export interface SidebarUser {
  name: string;
  email: string;
  roleLabel?: string | null | undefined;
}

interface SidebarProps {
  groups: NavGroup[];
  /** Rendered as an untitled group under the app's own — a product's own Settings or Help, kept
   *  where it already was rather than folded into `groups` (§ the migration report). */
  pinned?: NavEntry[] | undefined;
  /** Cross-subdomain links to the products this caller can open (`usePlatformModules`,
   *  `@opsgrids/client`) — real `<a href>`s, never a route. Omitted (or empty) hides the group,
   *  for the staff console, which has none. */
  modules?: ModuleLink[] | undefined;
  /** Users / Settings / Audit Logs (`useAdminLinks`), shown only when the caller holds the grant
   *  each one needs. Omitted hides the group entirely rather than showing it empty. */
  adminLinks?: ModuleLink[] | undefined;
  user: SidebarUser;
  /** The product's own mark, rendered at the top of the panel. */
  logo: ReactNode;
  productName: string;
  /**
   * The 72px icon rail (tablet, 768–1279px): labels move to `sr-only` and a native `title`
   * tooltip, group headings disappear, and the footer shows only the avatar. The same markup as
   * the full sidebar otherwise, so nothing drifts between the two.
   */
  collapsed?: boolean | undefined;
  /**
   * The tone of a `badge` pill. `accent` (default) is the violet attention pill; `danger` is for
   * a count that is a problem — overdue invoices — rather than merely new.
   */
  badgeTone?: "accent" | "danger" | undefined;
  onNavigate?: (() => void) | undefined;
}

function GroupLabel({ children, collapsed }: { children: ReactNode; collapsed: boolean }) {
  return (
    <p
      className={cn(
        "px-3 pb-1.5 text-caption font-bold uppercase tracking-wider text-faint",
        collapsed && "sr-only",
      )}
    >
      {children}
    </p>
  );
}

function NavRow({
  item,
  badgeTone,
  collapsed,
  onNavigate,
}: {
  item: NavEntry;
  badgeTone: "accent" | "danger";
  collapsed: boolean;
  onNavigate?: (() => void) | undefined;
}) {
  const Icon = item.icon;
  return (
    <NavLink
      to={item.to}
      end={item.end ?? false}
      onClick={onNavigate}
      title={collapsed ? item.label : undefined}
      className={({ isActive }) =>
        cn(
          "group relative flex items-center gap-3 rounded-tile h-10 text-sm font-semibold transition-colors",
          collapsed ? "justify-center px-2" : "pl-3 pr-2.5",
          isActive
            ? "bg-accent-soft text-accent-ink"
            : "text-muted hover:bg-ground hover:text-ink",
        )
      }
    >
      {({ isActive }) => (
        <>
          {isActive ? (
            <span
              className="absolute left-0 top-1/2 h-5 w-1 -translate-y-1/2 rounded-full bg-accent"
              aria-hidden
            />
          ) : null}
          <Icon
            className={cn(
              "size-5 shrink-0",
              isActive ? "text-accent" : "text-faint group-hover:text-ink",
            )}
          />
          <span className={cn("truncate", collapsed && "sr-only")}>{item.label}</span>
          {!collapsed && item.badge ? (
            <span
              className={cn(
                "ml-auto grid h-5 min-w-5 place-items-center rounded-full px-1.5 text-caption font-bold tnum",
                badgeTone === "danger" ? "bg-negative text-on-negative" : "bg-accent text-on-accent",
              )}
            >
              {item.badge}
            </span>
          ) : null}
          {!collapsed && item.count ? (
            <span className="ml-auto text-xs font-semibold text-faint tnum">{item.count}</span>
          ) : null}
        </>
      )}
    </NavLink>
  );
}

/**
 * A module or admin link: a real navigation, in-app when the href is same-origin relative
 * (`/admin/members`, the dashboard's own routes) and a plain cross-subdomain anchor otherwise
 * (`https://revenue.opsgrids.com`). The target decides what happens next — its own `RequireAuth`,
 * its own grants fetch — this is a courtesy pointer, not a guarantee.
 */
function LinkRow({
  link,
  collapsed,
  onNavigate,
}: {
  link: ModuleLink;
  collapsed: boolean;
  onNavigate?: (() => void) | undefined;
}) {
  const Icon = link.icon;
  const classes = (isActive: boolean) =>
    cn(
      "group flex items-center gap-3 rounded-tile h-10 text-sm font-semibold transition-colors",
      collapsed ? "justify-center px-2" : "pl-3 pr-2.5",
      isActive ? "bg-accent-soft text-accent-ink" : "text-muted hover:bg-ground hover:text-ink",
    );
  const content = (isActive: boolean) => (
    <>
      {Icon ? (
        <Icon
          className={cn("size-5 shrink-0", isActive ? "text-accent" : "text-faint group-hover:text-ink")}
        />
      ) : null}
      <span className={cn("truncate", collapsed && "sr-only")}>{link.label}</span>
    </>
  );

  if (link.href.startsWith("/")) {
    return (
      <NavLink
        to={link.href}
        onClick={onNavigate}
        title={collapsed ? link.label : undefined}
        className={({ isActive }) => classes(isActive)}
      >
        {({ isActive }) => content(isActive)}
      </NavLink>
    );
  }
  return (
    <a href={link.href} title={collapsed ? link.label : undefined} className={classes(false)}>
      {content(false)}
    </a>
  );
}

/**
 * The grouped left navigation, shared by every product: a brand header, this app's own nav
 * groups, a Modules group of cross-product links, an Admin group, and a footer identity card.
 * Everything that differs between products — the groups, the mark, the modules, the wording —
 * arrives as props.
 *
 * The same markup serves three places `AppShell` mounts it: the persistent desktop sidebar, the
 * tablet icon rail (`collapsed`) and the slide-over drawer both narrower widths open — so a fix or
 * a visual change lands in all three at once.
 */
export function Sidebar({
  groups,
  pinned,
  modules,
  adminLinks,
  user,
  logo,
  productName,
  collapsed = false,
  badgeTone = "accent",
  onNavigate,
}: SidebarProps) {
  return (
    <aside className="flex h-full w-full flex-col rounded-card bg-surface shadow-card">
      <div className={cn("flex items-center gap-2.5 border-b border-line p-3", collapsed && "justify-center")}>
        {logo}
        <span className={cn("truncate text-body font-bold text-ink", collapsed && "sr-only")}>
          {productName}
        </span>
      </div>

      <nav aria-label="Primary" className="flex-1 overflow-y-auto p-3">
        {groups.map((group) => (
          <div key={group.title} className="mb-4">
            <GroupLabel collapsed={collapsed}>{group.title}</GroupLabel>
            <div className="flex flex-col gap-0.5">
              {group.items.map((item) => (
                <NavRow
                  key={item.to}
                  item={item}
                  badgeTone={badgeTone}
                  collapsed={collapsed}
                  onNavigate={onNavigate}
                />
              ))}
            </div>
          </div>
        ))}

        {pinned && pinned.length > 0 ? (
          <div className="mb-4 flex flex-col gap-0.5 border-t border-line pt-3">
            {pinned.map((item) => (
              <NavRow
                key={item.to}
                item={item}
                badgeTone={badgeTone}
                collapsed={collapsed}
                onNavigate={onNavigate}
              />
            ))}
          </div>
        ) : null}

        {modules && modules.length > 0 ? (
          <div className="mb-4">
            <GroupLabel collapsed={collapsed}>Modules</GroupLabel>
            <div className="flex flex-col gap-0.5">
              {modules.map((link) => (
                <LinkRow key={link.key} link={link} collapsed={collapsed} onNavigate={onNavigate} />
              ))}
            </div>
          </div>
        ) : null}

        {adminLinks && adminLinks.length > 0 ? (
          <div className="mb-4">
            <GroupLabel collapsed={collapsed}>Admin</GroupLabel>
            <div className="flex flex-col gap-0.5">
              {adminLinks.map((link) => (
                <LinkRow key={link.key} link={link} collapsed={collapsed} onNavigate={onNavigate} />
              ))}
            </div>
          </div>
        ) : null}
      </nav>

      <div className={cn("flex items-center gap-3 border-t border-line p-3", collapsed && "justify-center")}>
        <Avatar name={user.name} size="sm" />
        {collapsed ? (
          <span className="sr-only">
            {user.name}
            {user.roleLabel ? `, ${user.roleLabel}` : ""}
          </span>
        ) : (
          <div className="flex min-w-0 flex-col leading-tight">
            <span className="truncate text-sm font-bold text-ink">{user.name}</span>
            <span className="truncate text-xs text-muted">{user.roleLabel ?? user.email}</span>
          </div>
        )}
      </div>
    </aside>
  );
}
