import { Outlet, useLocation } from "react-router-dom";
import { useEffect } from "react";

import { TopNav } from "./ui";

/**
 * The app shell: a floating top nav over the light-grey ground, with the routed screen below.
 * Scroll resets on navigation so a new screen starts at the top.
 */
export function AppShell() {
  const { pathname } = useLocation();

  useEffect(() => {
    window.scrollTo({ top: 0 });
  }, [pathname]);

  return (
    <div className="min-h-dvh bg-ground pb-16">
      <TopNav />
      <main className="mx-auto max-w-[1400px] px-3 sm:px-6 pt-6 sm:pt-8">
        <Outlet />
      </main>
    </div>
  );
}
