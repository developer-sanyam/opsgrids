import { useCallback, useEffect, useState } from "react";
import type { RefObject } from "react";

/**
 * Where focus goes when the control someone just used is about to disappear.
 *
 * A screen that re-reads itself after a write (`useAsync`'s `reload`) puts its content back to a
 * loading skeleton, which unmounts the button or row the person was on — and the browser drops
 * focus to the document, so a keyboard or screen-reader user starts again from the top with no idea
 * what happened. The returned function sends focus to the page heading (`PageTitle`) instead, so
 * the very next thing read is where they are.
 *
 * **It focuses from an effect, after the commit, and not from the event handler.** A confirm dialog
 * that has just closed returns focus to the control that opened it, and does so while the commit
 * that closes it is being torn down; a `focus()` call made from the handler, before that commit,
 * would be undone by it. Asking for focus through state means the heading is focused in the same
 * commit but after the dialog's own cleanup has run, so the heading wins — and it does not depend
 * on the screen's read taking long enough to show a loading state, which a fast answer never does.
 */
export function useFocusHeading(heading: RefObject<HTMLElement | null>): () => void {
  const [requests, setRequests] = useState(0);

  useEffect(() => {
    if (requests > 0) heading.current?.focus();
  }, [requests, heading]);

  return useCallback(() => setRequests((count) => count + 1), []);
}
