import { useEffect } from "react";
import type { RefObject } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { useAnnounce } from "@opsgrids/ui";

/**
 * Say, once, what the screen that navigated here just did — and put focus where a person arriving
 * from it can tell they have arrived.
 *
 * A screen that deletes or creates something and then navigates away cannot announce the result
 * into its own live region — it is unmounted before the region is read — and the control they used
 * is gone, so focus would fall to the document. It hands the sentence to the destination as
 * `navigate(to, { state: { notice } })`; the destination announces it into its own region and moves
 * focus to its heading (`focusTarget`, a `PageHeading`).
 *
 * The notice is then cleared from the history entry. Router state survives a reload, and a page
 * that announced "October update deleted." again every time it was refreshed would be saying
 * something that stopped being news. Anything that is not a string in that slot is ignored,
 * because router state is whatever the last caller put there.
 */
function noticeFrom(state: unknown): string | null {
  if (typeof state !== "object" || state === null || !("notice" in state)) return null;
  return typeof state.notice === "string" ? state.notice : null;
}

export function useRouteNotice(focusTarget?: RefObject<HTMLElement | null>): void {
  const announce = useAnnounce();
  const navigate = useNavigate();
  const { pathname, search, hash, state } = useLocation();
  const notice = noticeFrom(state);

  useEffect(() => {
    if (notice === null) return;
    announce(notice);
    focusTarget?.current?.focus();
    navigate({ pathname, search, hash }, { replace: true, state: null });
  }, [notice, announce, focusTarget, navigate, pathname, search, hash]);
}
