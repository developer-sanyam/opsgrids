import { useEffect, useRef, useState } from "react";
import type { ComponentType, SVGProps } from "react";
import { NavLink, useNavigate } from "react-router-dom";

import { useAuth } from "@/auth/context";
import { cn } from "@/lib/cn";
import { initials } from "@/lib/format";
import { ThemeToggle } from "./ThemeToggle";
import {
  BuildingsIcon,
  ChevronsUpDown,
  CommandIcon,
  GaugeIcon,
  GiftIcon,
  LayersIcon,
  LogoMark,
  LogOutIcon,
  SearchIcon,
  ShieldIcon,
  TagIcon,
} from "./icons";

type IconType = ComponentType<SVGProps<SVGSVGElement>>;

interface NavEntry {
  to: string;
  label: string;
  icon: IconType;
  end?: boolean;
}

const GROUPS: { title: string; items: NavEntry[] }[] = [
  {
    title: "Console",
    items: [
      { to: "/", label: "Overview", icon: GaugeIcon, end: true },
      { to: "/accounts", label: "Accounts", icon: BuildingsIcon },
      { to: "/subscriptions", label: "Subscriptions", icon: LayersIcon },
    ],
  },
  {
    title: "Commerce",
    items: [
      { to: "/plans", label: "Plans & Pricing", icon: TagIcon },
      { to: "/grants", label: "Grants & Credits", icon: GiftIcon },
    ],
  },
  {
    title: "Governance",
    items: [{ to: "/audit", label: "Audit log", icon: ShieldIcon }],
  },
];

const ROLE_LABEL: Record<string, string> = {
  revenue_ops: "Revenue Ops",
  support: "Support",
  billing_admin: "Billing Admin",
  read_only: "Read only",
};

function NavRow({ item, onNavigate }: { item: NavEntry; onNavigate?: (() => void) | undefined }) {
  const Icon = item.icon;
  return (
    <NavLink
      to={item.to}
      end={item.end ?? false}
      onClick={onNavigate}
      className={({ isActive }) =>
        cn(
          "group relative flex h-10 items-center gap-3 rounded-tile pl-3 pr-2.5 text-sm font-semibold transition-colors",
          isActive ? "bg-accent-soft text-accent-ink" : "text-muted hover:bg-ground hover:text-ink",
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
            className={cn("size-5 shrink-0", isActive ? "text-accent" : "text-faint group-hover:text-ink")}
          />
          <span className="truncate">{item.label}</span>
        </>
      )}
    </NavLink>
  );
}

/** The account switcher at the top: operator identity, role sub-label, and a menu with sign out. */
function AccountSwitcher() {
  const { operator, logout } = useAuth();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onPointer(event: MouseEvent) {
      if (ref.current && !ref.current.contains(event.target as Node)) setOpen(false);
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const name = operator?.full_name ?? "Operator";
  const roles = operator?.operator_roles ?? [];
  const roleText = roles.map((r) => ROLE_LABEL[r] ?? r).join(" · ") || "Operator";

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={open}
        className="flex h-14 w-full items-center gap-2.5 rounded-tile border border-line bg-surface pl-2 pr-2.5 hover:bg-ground transition-colors"
      >
        <span className="grid size-10 shrink-0 place-items-center rounded-tile bg-accent text-sm font-bold text-white">
          {initials(name)}
        </span>
        <span className="flex min-w-0 flex-col items-start leading-tight">
          <span className="truncate text-sm font-bold text-ink">{name}</span>
          <span className="truncate text-xs text-muted">{roleText}</span>
        </span>
        <ChevronsUpDown className="ml-auto size-4 shrink-0 text-faint" />
      </button>

      {open ? (
        <div
          role="menu"
          className="absolute left-0 right-0 top-full z-40 mt-2 rounded-card bg-surface p-2 shadow-float"
        >
          <div className="flex items-center gap-3 px-2 py-2">
            <span className="grid size-10 place-items-center rounded-tile bg-ink text-sm font-bold text-surface">
              {initials(name)}
            </span>
            <div className="flex min-w-0 flex-col leading-tight">
              <span className="truncate text-sm font-semibold text-ink">{name}</span>
              <span className="truncate text-xs text-muted">{operator?.email ?? ""}</span>
            </div>
          </div>
          <div className="mx-1 mb-1 rounded-tile bg-ground px-3 py-2">
            <span className="block text-[0.7rem] font-semibold uppercase tracking-wide text-faint">
              Operator roles
            </span>
            <span className="block truncate text-sm font-semibold text-ink">{roleText}</span>
          </div>
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              setOpen(false);
              void logout();
            }}
            className="flex h-10 w-full items-center gap-2.5 rounded-tile px-3 text-sm font-semibold text-ink hover:bg-ground transition-colors"
          >
            <LogOutIcon className="size-5 text-muted" />
            Sign out
          </button>
        </div>
      ) : null}
    </div>
  );
}

/**
 * The grouped left navigation for the operator console: an "INTERNAL" wordmark, the account
 * switcher, a ⌘K search that jumps to the cross-tenant accounts table, the nav groups with an
 * active violet marker, and a footer card that keeps the control-panel identity always visible.
 */
export function Sidebar({ onNavigate }: { onNavigate?: () => void }) {
  const navigate = useNavigate();
  const searchRef = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState("");

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        searchRef.current?.focus();
      }
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  function submitSearch(event: React.FormEvent) {
    event.preventDefault();
    const q = query.trim();
    navigate(q ? `/accounts?q=${encodeURIComponent(q)}` : "/accounts");
    onNavigate?.();
  }

  return (
    <aside className="flex h-full w-full flex-col rounded-card bg-surface shadow-card">
      <div className="flex items-center gap-2.5 px-4 pb-1 pt-4">
        <LogoMark className="size-8" />
        <span className="text-[1.05rem] font-extrabold tracking-tight">OpsGrids</span>
        <span className="mono rounded-md bg-accent-soft px-1.5 py-0.5 text-[0.62rem] font-semibold uppercase tracking-widest text-accent-ink">
          Internal
        </span>
      </div>

      <div className="p-3">
        <AccountSwitcher />
      </div>

      <div className="px-3 pb-2">
        <form
          onSubmit={submitSearch}
          className="flex h-10 items-center gap-2 rounded-full bg-ground px-3 focus-within:outline focus-within:outline-2 focus-within:outline-accent focus-within:outline-offset-2"
        >
          <SearchIcon className="size-[18px] shrink-0 text-faint" />
          <label htmlFor="sidebar-search" className="sr-only">
            Search accounts
          </label>
          <input
            id="sidebar-search"
            ref={searchRef}
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search accounts"
            className="min-w-0 flex-1 bg-transparent text-sm text-ink placeholder:text-faint focus:outline-none"
          />
          <kbd className="hidden shrink-0 items-center gap-0.5 rounded-md border border-line bg-surface px-1.5 py-0.5 text-[0.65rem] font-semibold text-faint sm:inline-flex">
            <CommandIcon className="size-3" />K
          </kbd>
        </form>
      </div>

      <nav aria-label="Primary" className="flex-1 overflow-y-auto px-3 pb-2">
        {GROUPS.map((group) => (
          <div key={group.title} className="mb-4">
            <p className="px-3 pb-1.5 text-[0.68rem] font-bold uppercase tracking-wider text-faint">
              {group.title}
            </p>
            <div className="flex flex-col gap-0.5">
              {group.items.map((item) => (
                <NavRow key={item.to} item={item} onNavigate={onNavigate} />
              ))}
            </div>
          </div>
        ))}
      </nav>

      <div className="border-t border-line p-3">
        <div className="flex items-center gap-3 rounded-tile bg-ground px-3 py-2.5">
          <span className="grid size-8 shrink-0 place-items-center rounded-tile bg-ink text-surface">
            <ShieldIcon className="size-4" />
          </span>
          <div className="flex min-w-0 flex-col leading-tight">
            <span className="truncate text-sm font-bold text-ink">Operator console</span>
            <span className="truncate text-xs text-muted">Cross-tenant control panel</span>
          </div>
          <ThemeToggle className="ml-auto" />
        </div>
      </div>
    </aside>
  );
}
