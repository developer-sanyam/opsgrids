import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Outlet, useLocation } from "react-router-dom";

import { CloseIcon, MenuIcon } from "../icons";
import { useFocusTrap } from "../lib/useFocusTrap";
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
  const drawerRef = useRef<HTMLDivElement>(null);
  const drawerCloseRef = useRef<HTMLButtonElement>(null);
  const mainRef = useRef<HTMLElement>(null);
  const isFirstRender = useRef(true);

  useEffect(() => {
    window.scrollTo({ top: 0 });
    setDrawerOpen(false);
    // Skip on mount: the initial screen owns its own focus (the browser's default, or wherever a
    // deep link put it). Only a route *change* should steal focus onto `<main>`.
    if (isFirstRender.current) {
      isFirstRender.current = false;
      return;
    }
    mainRef.current?.focus();
  }, [pathname]);

  useFocusTrap({
    open: drawerOpen,
    panelRef: drawerRef,
    onClose: () => setDrawerOpen(false),
    initialFocusRef: drawerCloseRef,
  });

  return (
    <div className="min-h-dvh bg-ground">
      <a
        href="#main-content"
        onClick={(event) => {
          // The default fragment jump moves the page's scroll position but not focus in every
          // browser, and never announces anything to a screen reader. Doing it here guarantees
          // both: the skip link's entire job is to hand focus straight to `<main>`.
          event.preventDefault();
          mainRef.current?.focus();
        }}
        className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-[60] focus:rounded-full focus:bg-surface focus:px-4 focus:py-2 focus:text-sm focus:font-bold focus:text-ink focus:shadow-float"
      >
        Skip to main content
      </a>
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

          <main
            id="main-content"
            ref={mainRef}
            tabIndex={-1}
            className="mx-auto max-w-[1400px] pb-16 focus:outline-none"
          >
            <Outlet />
          </main>
        </div>
      </div>

      {/* Mobile drawer */}
      {drawerOpen ? (
        <div
          ref={drawerRef}
          className="fixed inset-0 z-50 lg:hidden"
          role="dialog"
          aria-modal="true"
          aria-label="Navigation"
        >
          <div
            className="absolute inset-0 bg-scrim"
            onClick={() => setDrawerOpen(false)}
            aria-hidden
          />
          <div className="absolute left-0 top-0 flex h-full w-[280px] max-w-[85vw] flex-col p-3">
            <div className="mb-2 flex justify-end">
              <button
                ref={drawerCloseRef}
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
