/**
 * The Deals count in the sidebar lives in the shell, far from the screens that add and remove
 * deals. A screen that changes how many deals exist says so here; the sidebar listens and asks
 * the server again. No count is cached or copied anywhere: the event only means "ask again".
 */

const EVENT = "crm:deals-changed";

/** Call after a deal is created, deleted or bulk-imported. */
export function announceDealsChanged(): void {
  window.dispatchEvent(new Event(EVENT));
}

/** Subscribe to {@link announceDealsChanged}; returns the unsubscribe function. */
export function onDealsChanged(listener: () => void): () => void {
  window.addEventListener(EVENT, listener);
  return () => window.removeEventListener(EVENT, listener);
}
