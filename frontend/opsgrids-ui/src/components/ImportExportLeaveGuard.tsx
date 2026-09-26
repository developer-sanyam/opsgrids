import { useCallback, useContext, useEffect } from "react";
import { UNSAFE_DataRouterContext, useBlocker } from "react-router-dom";
import type { BlockerFunction } from "react-router-dom";

import { ConfirmDialog } from "./ConfirmDialog";

interface LeaveGuardProps {
  /** Rows are going out: leaving the page would stop the import part-way. */
  importing: boolean;
  /** Stops the import after the row in flight. Called once, just before a confirmed navigation. */
  onStop: () => void;
}

function Guard({ importing, onStop }: LeaveGuardProps) {
  // Only a change of page counts. The screen stays mounted through a change of its own query
  // string, and an import that is still running is not at risk from that.
  const changesPage = useCallback<BlockerFunction>(
    ({ currentLocation, nextLocation }) => importing && currentLocation.pathname !== nextLocation.pathname,
    [importing],
  );
  const blocker = useBlocker(changesPage);

  // The import ended while the question was up: it is no longer true, and the result the user has
  // not seen yet is the more important thing on the screen. They stay, and can leave from there.
  useEffect(() => {
    if (!importing && blocker.state === "blocked") blocker.reset();
  }, [importing, blocker]);

  return (
    <ConfirmDialog
      open={blocker.state === "blocked"}
      onClose={() => {
        if (blocker.state === "blocked") blocker.reset();
      }}
      onConfirm={() => {
        if (blocker.state !== "blocked") return;
        // Stopped by the user, so the runner marks the summary cancelled; the screen going away
        // would otherwise dispose the run without a word.
        onStop();
        blocker.proceed();
      }}
      title="Stop the import and leave?"
      body="An import is running. Leaving now stops it after the row being sent; rows already sent stay created."
      confirmLabel="Leave and stop"
      cancelLabel="Stay"
    />
  );
}

/**
 * The in-app half of "keep this tab open" (the reload and close-the-tab half is the browser's own
 * prompt, `useUnsavedChangesWarning`).
 *
 * Browser Back or a link to another page unmounts the screen, and the data-exchange hook stops a
 * run whose screen is gone, so five rows in, the import would just end, with no warning and no
 * summary. While `importing`, react-router's blocker holds such a navigation and asks in a
 * `ConfirmDialog` stacked over the import dialog: Stay (focused, and what Escape means) or Leave and
 * stop. `UnsavedChangesGuard` is the same pattern for forms.
 *
 * `useBlocker` needs a data router (`createBrowserRouter` with `RouterProvider`), which every app
 * builds. Rendered anywhere else (a workshop story, a test, a plain `<BrowserRouter>`) it would
 * throw, so the guard renders nothing there and the import runs unguarded: safe, and the browser's
 * own prompt still covers leaving the site.
 */
export function ImportExportLeaveGuard(props: LeaveGuardProps) {
  const inDataRouter = useContext(UNSAFE_DataRouterContext) !== null;
  return inDataRouter ? <Guard {...props} /> : null;
}
