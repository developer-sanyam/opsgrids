import { useCallback, useMemo, useState } from "react";
import type { ReactNode } from "react";

import { AnnouncerContext } from "../lib/announcer";

/**
 * A screen's single polite live region.
 *
 * Every screen mounts one and everything inside it announces through `useAnnounce()` — never one
 * region per control, which is how a screen ends up announcing the same sentence three times.
 * Errors that replace a region use `role="alert"` (`ErrorState` does) and are deliberately *not*
 * repeated here: hearing a failure twice is worse than hearing it once.
 *
 * Two regions alternate by sequence number because assistive technology does not re-announce text
 * identical to what a region already holds — "Loaded 25 more" twice in a row would be said once.
 */
export function Announcer({ children }: { children: ReactNode }) {
  const [state, setState] = useState<{ message: string; seq: number }>({
    message: "",
    seq: 0,
  });

  const announce = useCallback((message: string) => {
    setState((prev) => ({ message, seq: prev.seq + 1 }));
  }, []);

  const value = useMemo(() => announce, [announce]);
  const even = state.seq % 2 === 0;

  return (
    <AnnouncerContext value={value}>
      {children}
      <div className="sr-only">
        <p aria-live="polite" aria-atomic="true">
          {even ? "" : state.message}
        </p>
        <p aria-live="polite" aria-atomic="true">
          {even ? state.message : ""}
        </p>
      </div>
    </AnnouncerContext>
  );
}
