import { useEffect, useState } from "react";
import { Outlet, useLocation } from "react-router-dom";

import { EnvBadge, Sidebar, ThemeToggle } from "./ui";
import { CloseIcon, LogoMark, MenuIcon } from "./ui/icons";

/**
 * The console shell: a grouped left sidebar over the light-grey ground, with the routed screen in
 * the main column. The content column is wide — the console is data-dense — and a faint grid field
 * gives it the control-panel atmosphere. On narrow screens the sidebar collapses into a slide-over
 * drawer opened from a compact top bar. Scroll resets and the drawer closes on navigation.
 */
export function AppShell() {
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
      <div className="mx-auto flex max-w-[1760px] gap-4 p-3 sm:p-4">
        {/* Desktop sidebar */}
        <div className="hidden w-[256px] shrink-0 lg:block">
          <div className="sticky top-4 h-[calc(100dvh-2rem)]">
            <Sidebar />
          </div>
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
            <LogoMark className="size-7" />
            <span className="text-[1.05rem] font-extrabold tracking-tight">OpsGrids</span>
            <span className="mono rounded-md bg-accent-soft px-1.5 py-0.5 text-[0.6rem] font-semibold uppercase tracking-widest text-accent-ink">
              Internal
            </span>
            <div className="flex-1" />
            <ThemeToggle />
          </div>

          <main className="mx-auto max-w-[1560px] pb-16">
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
          <div className="absolute inset-0 bg-ink/40" onClick={() => setDrawerOpen(false)} aria-hidden />
          <div className="absolute left-0 top-0 flex h-full w-[288px] max-w-[85vw] flex-col p-3">
            <div className="mb-2 flex items-center justify-between">
              <EnvBadge />
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
              <Sidebar onNavigate={() => setDrawerOpen(false)} />
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
