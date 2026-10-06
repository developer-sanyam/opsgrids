/**
 * Loading a payment provider's own SDK: one `<script>` from the provider's origin, added once per
 * page and never bundled. A script that fails to load — an ad blocker, a corporate proxy, a dropped
 * connection — or that never answers is reported as `CheckoutUnavailableError`, and the next
 * attempt starts clean rather than waiting on a dead element.
 */

/** The provider's payment window could not be opened. Nothing was charged by this page. */
export class CheckoutUnavailableError extends Error {
  constructor(provider: string) {
    super(
      `${provider}'s payment window could not be opened. A browser extension or your network may be blocking it — allow it, or try another browser, then pay again.`,
    );
    this.name = "CheckoutUnavailableError";
  }
}

const LOAD_TIMEOUT_MS = 15_000;

const pending = new Map<string, Promise<void>>();

/**
 * Resolve once `src` has loaded and `ready()` sees the global it defines. Concurrent callers share
 * one load; a failed load forgets itself and removes its element.
 */
export function loadScriptOnce(src: string, ready: () => boolean, provider: string): Promise<void> {
  if (ready()) return Promise.resolve();
  const inFlight = pending.get(src);
  if (inFlight) return inFlight;

  const load = new Promise<void>((resolve, reject) => {
    const script = document.createElement("script");
    script.src = src;
    script.async = true;
    let settled = false;
    const fail = () => {
      if (settled) return;
      settled = true;
      window.clearTimeout(timer);
      pending.delete(src);
      script.remove();
      reject(new CheckoutUnavailableError(provider));
    };
    const timer = window.setTimeout(fail, LOAD_TIMEOUT_MS);
    script.onload = () => {
      if (settled) return;
      if (!ready()) {
        fail();
        return;
      }
      settled = true;
      window.clearTimeout(timer);
      resolve();
    };
    script.onerror = fail;
    document.head.appendChild(script);
  });
  pending.set(src, load);
  return load;
}

/** Test seam: forget every cached load so a test can simulate a fresh page. */
export function resetScriptLoadsForTests(): void {
  pending.clear();
}
