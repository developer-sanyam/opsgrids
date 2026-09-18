import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { Outlet, useLocation } from "react-router-dom";

import { CloseIcon, MenuIcon } from "../icons";
import { ThemeToggle } from "./ThemeToggle";

interface AppShellProps {
  /** The product's mark, shown in the compact mobile bar. */
  logo: ReactNode;
  /** The product's name, shown beside the mark on narrow screens. */
  productName: string;
  /**
   * Renders the left navigation. Called twice — once for the desktop rail and once inside the
   * mobile drawer — so the drawer variant can close itself on navigation.
   */
  renderSidebar: (options: { onNavigate?: () => void }) => ReactNode;
}

/**
 * The app shell: a grouped left sidebar over the light-grey ground, with the routed screen in the
 * main column. On narrow screens the sidebar collapses into a slide-over drawer opened from a
 * compact top bar. Scroll resets and the drawer closes on navigation.
 */
export function AppShell({ logo, productName, renderSidebar }: AppShellProps) {
  const { pathname } = useLocation();
  const [drawerOpen, setDrawerOpen] = useState(false);

  useEffect(() => {
    window.scrollTo({ top: 0 });
    setDrawerOpen(false);
  }, [pathname]);

  useEffect(() => {
    if (!drawerOpen) return;
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") setDrawerOpen(false);
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [drawerOpen]);

  return (
    <div className="min-h-dvh bg-ground">
      <div className="mx-auto flex max-w-[1600px] gap-4 p-3 sm:p-4">
        {/* Desktop sidebar */}
        <div className="hidden w-[248px] shrink-0 lg:block">
          <div className="sticky top-4 h-[calc(100dvh-2rem)]">{renderSidebar({})}</div>
        </div>

        {/* Main column */}
        <div className="min-w-0 flex-1">
          {/* Mobile top bar */}
          <div className="mb-3 flex h-14 items-center gap-2 rounded-card bg-surface px-3 shadow-card lg:hidden">
            <button
              type="button"
              onClick={() => setDrawerOpen(true)}
              aria-label="Open navigation"
              className="grid size-10 place-items-center rounded-full text-ink hover:bg-ground transition-colors"
            >
              <MenuIcon className="size-5" />
            </button>
            {logo}
            <span className="text-[1.05rem] font-extrabold tracking-tight">{productName}</span>
            <div className="flex-1" />
            <ThemeToggle />
          </div>

          <main className="mx-auto max-w-[1400px] pb-16">
            <Outlet />
          </main>
        </div>
      </div>

      {/* Mobile drawer */}
      {drawerOpen ? (
        <div
          className="fixed inset-0 z-50 lg:hidden"
          role="dialog"
          aria-modal="true"
          aria-label="Navigation"
        >
          <div
            className="absolute inset-0 bg-ink/40"
            onClick={() => setDrawerOpen(false)}
            aria-hidden
          />
          <div className="absolute left-0 top-0 flex h-full w-[280px] max-w-[85vw] flex-col p-3">
            <div className="mb-2 flex justify-end">
              <button
                type="button"
                onClick={() => setDrawerOpen(false)}
                aria-label="Close navigation"
                className="grid size-9 place-items-center rounded-full bg-surface text-ink shadow-card"
              >
                <CloseIcon className="size-5" />
              </button>
            </div>
            <div className="min-h-0 flex-1">
              {renderSidebar({ onNavigate: () => setDrawerOpen(false) })}
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
