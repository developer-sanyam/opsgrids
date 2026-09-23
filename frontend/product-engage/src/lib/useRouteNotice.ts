import { useEffect } from "react";
import { useLocation } from "react-router-dom";

import { useAnnounce } from "@opsgrids/ui";

/**
 * Say, once, what the screen that navigated here just did.
 *
 * A screen that deletes or creates something and then navigates away cannot announce the result
 * into its own live region — it is unmounted before the region is read. It hands the sentence to
 * the destination as `navigate(to, { state: { notice } })`, and the destination announces it into
 * its own. Anything that is not a string in that slot is ignored, because router state is whatever
 * the last caller put there.
 */
function noticeFrom(state: unknown): string | null {
  if (typeof state !== "object" || state === null || !("notice" in state)) return null;
  return typeof state.notice === "string" ? state.notice : null;
}

export function useRouteNotice(): void {
  const announce = useAnnounce();
  const notice = noticeFrom(useLocation().state);
  useEffect(() => {
    if (notice !== null) announce(notice);
  }, [notice, announce]);
}
