import { useEffect, useRef, useState } from "react";
import { NavLink } from "react-router-dom";

import { useAuth } from "@/auth/context";
import { cn } from "@/lib/cn";
import { initials } from "@/lib/format";
import {
  ChevronDown,
  LogOutIcon,
  LogoMark,
  MenuIcon,
  SettingsIcon,
} from "./icons";

interface NavItem {
  to: string;
  label: string;
  soon?: boolean;
}

const NAV: NavItem[] = [
  { to: "/", label: "Dashboard" },
  { to: "/employees", label: "Employees" },
  { to: "/attendance", label: "Attendance" },
  { to: "/leave", label: "Leave", soon: true },
  { to: "/payroll", label: "Payroll", soon: true },
];

function tabClass(active: boolean): string {
  return cn(
    "relative inline-flex items-center gap-1.5 rounded-full px-4 h-9 text-sm font-semibold whitespace-nowrap transition-colors",
    active ? "bg-mint text-ink" : "text-muted hover:bg-ground hover:text-ink",
  );
}

/** The avatar menu: identity header plus a working Sign out. Closes on outside click or Escape. */
function AccountMenu() {
  const { user, logout } = useAuth();
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

  const name = user?.full_name ?? "Account";
  const email = user?.email ?? "";
  const org =
    user?.organizations.find((o) => o.organization_id === user.organization_id) ??
    user?.organizations[0] ??
    null;

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={open}
        className="flex items-center gap-2.5 rounded-full bg-ground pl-1.5 pr-3 h-11 hover:bg-line transition-colors"
      >
        <span className="grid size-8 place-items-center rounded-full bg-ink text-white text-xs font-bold">
          {initials(name)}
        </span>
        <span className="hidden lg:flex flex-col items-start leading-tight">
          <span className="text-sm font-semibold">{name}</span>
          <span className="text-xs text-muted">{email}</span>
        </span>
        <ChevronDown
          className={cn("size-4 text-muted transition-transform", open && "rotate-180")}
        />
      </button>

      {open ? (
        <div
          role="menu"
          className="absolute right-0 mt-2 w-64 rounded-card bg-surface p-2 z-40"
          style={{ boxShadow: "var(--shadow-float)" }}
        >
          <div className="flex items-center gap-3 px-2 py-2">
            <span className="grid size-10 place-items-center rounded-full bg-ink text-white text-sm font-bold">
              {initials(name)}
            </span>
            <div className="flex min-w-0 flex-col leading-tight">
              <span className="truncate text-sm font-semibold">{name}</span>
              <span className="truncate text-xs text-muted">{email}</span>
            </div>
          </div>

          {org ? (
            <div className="mx-2 mb-1 rounded-tile bg-ground px-3 py-2">
              <span className="block text-[0.7rem] font-semibold uppercase tracking-wide text-faint">
                Organization
              </span>
              <span className="block truncate text-sm font-semibold text-ink">{org.name}</span>
            </div>
          ) : null}

          <button
            type="button"
            role="menuitem"
            onClick={() => {
              setOpen(false);
              void logout();
            }}
            className="mt-1 flex w-full items-center gap-2.5 rounded-tile px-3 h-10 text-sm font-semibold text-ink hover:bg-ground transition-colors"
          >
            <LogOutIcon className="size-5 text-muted" />
            Sign out
          </button>
        </div>
      ) : null}
    </div>
  );
}

export function TopNav() {
  const [open, setOpen] = useState(false);

  return (
    <div className="sticky top-3 z-30 px-3 sm:px-6">
      <nav
        className="mx-auto flex max-w-[1400px] items-center gap-3 rounded-full bg-surface px-3 sm:px-4 h-16"
        style={{ boxShadow: "var(--shadow-float)" }}
        aria-label="Primary"
      >
        {/* Brand */}
        <NavLink
          to="/"
          className="flex items-center gap-2.5 pl-1 pr-2 shrink-0"
          aria-label="Atmora HR home"
        >
          <LogoMark className="size-8" />
          <span className="hidden sm:block text-[1.05rem] font-extrabold tracking-tight">
            Atmora HR
          </span>
        </NavLink>

        {/* Desktop tabs */}
        <div className="hidden md:flex items-center gap-1 ml-1">
          {NAV.map((item) => (
            <NavLink key={item.to} to={item.to} end={item.to === "/"} className={({ isActive }) => tabClass(isActive)}>
              {item.label}
              {item.soon ? (
                <span className="size-1.5 rounded-full bg-mint-strong" aria-hidden />
              ) : null}
            </NavLink>
          ))}
        </div>

        <div className="flex-1" />

        {/* Right cluster */}
        <button
          type="button"
          aria-label="Settings"
          className="hidden sm:grid size-10 place-items-center rounded-full text-ink-soft hover:bg-ground transition-colors"
        >
          <SettingsIcon className="size-5" />
        </button>

        <AccountMenu />

        {/* Mobile menu toggle */}
        <button
          type="button"
          className="md:hidden grid size-10 place-items-center rounded-full text-ink hover:bg-ground transition-colors"
          aria-label="Toggle navigation"
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
        >
          <MenuIcon className="size-5" />
        </button>
      </nav>

      {/* Mobile dropdown */}
      {open ? (
        <div
          className="md:hidden mx-auto mt-2 max-w-[1400px] rounded-card bg-surface p-2"
          style={{ boxShadow: "var(--shadow-float)" }}
        >
          <div className="flex flex-col">
            {NAV.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.to === "/"}
                onClick={() => setOpen(false)}
                className={({ isActive }) =>
                  cn(
                    "flex items-center justify-between rounded-full px-4 h-11 text-sm font-semibold transition-colors",
                    isActive ? "bg-mint text-ink" : "text-ink-soft hover:bg-ground",
                  )
                }
              >
                {item.label}
                {item.soon ? (
                  <span className="text-xs font-medium text-muted">Soon</span>
                ) : null}
              </NavLink>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}
