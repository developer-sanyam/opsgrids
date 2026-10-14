import type { ReactNode } from "react";

import { MenuIcon, SearchIcon } from "../icons";

interface MobileHeaderProps {
  logo: ReactNode;
  productName: string;
  onOpenNav: () => void;
  /** Opens the same ⌘K command menu the desktop header's search trigger does. Omitted when the
   *  app passes no `search` config to `AppShell` — never a dead icon. */
  search?: { placeholder: string; onOpen: () => void } | undefined;
  /** Renders nothing when omitted — there is no per-user notification feed yet (the report's API
   *  gaps), and a bell with no feed behind it is worse than no bell. */
  notifications?: ReactNode | undefined;
  /** The same account menu the desktop header shows, handed down as a slot so this component
   *  stays presentational. */
  profile: ReactNode;
}

/**
 * The compact top bar below 768px: a menu button that opens the full navigation drawer, the
 * product's mark, a search trigger, the notifications slot and the account menu. A `header`
 * landmark, not a bare div — the audit's `region` violations were exactly this content sitting
 * outside one.
 */
export function MobileHeader({
  logo,
  productName,
  onOpenNav,
  search,
  notifications,
  profile,
}: MobileHeaderProps) {
  return (
    <header className="shell-mobile-header-h sticky top-0 z-30 flex items-center gap-1 border-b border-line bg-surface md:hidden">
      <button
        type="button"
        onClick={onOpenNav}
        aria-label="Open navigation"
        className="grid size-11 shrink-0 place-items-center rounded-full text-ink transition-colors hover:bg-ground"
      >
        <MenuIcon className="size-5" />
      </button>
      {logo}
      <span className="truncate text-sm font-bold tracking-tight text-ink">{productName}</span>
      <div className="flex-1" />
      {search ? (
        <button
          type="button"
          onClick={search.onOpen}
          aria-label={search.placeholder}
          className="grid size-11 shrink-0 place-items-center rounded-full text-ink-soft transition-colors hover:bg-ground"
        >
          <SearchIcon className="size-5" />
        </button>
      ) : null}
      {notifications}
      {profile}
    </header>
  );
}
