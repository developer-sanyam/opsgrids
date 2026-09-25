import { useEffect } from "react";

/**
 * Asks the browser to confirm before the page is reloaded, closed or navigated off-site while
 * `dirty` is true — the brief's "confirm before leaving with unsaved changes" (§8–9) for the
 * one kind of leaving an app cannot intercept itself.
 *
 * The browser shows its own fixed sentence; no custom text survives any current browser, so none
 * is offered. Leaving *within* the app (a sidebar link) never fires `beforeunload` in a
 * single-page app — `UnsavedChangesGuard` covers that half with a real dialog.
 *
 * Installed only while dirty. A listener left on permanently disables the back-forward cache for
 * the page, and prompts on a form nobody has touched train people to click through it.
 */
export function useUnsavedChangesWarning(dirty: boolean): void {
  useEffect(() => {
    if (!dirty) return;
    function onBeforeUnload(event: BeforeUnloadEvent) {
      event.preventDefault();
      // Chrome before 119 and Safari still need the legacy assignment to show the prompt.
      event.returnValue = "";
    }
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [dirty]);
}
