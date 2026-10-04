import { useCallback, useRef } from "react";

/**
 * One `Idempotency-Key` per distinct request a dialog makes.
 *
 * The same body gets the same key, so a double submit or a retry after a dropped response replays
 * the posting instead of making it twice; a corrected body is a different request and gets a new
 * key. The key lives in a ref for the life of the dialog and is gone when it closes, which is the
 * right lifetime: reopening the dialog is a new intention.
 */
export function useIdempotencyKey(): (body: unknown) => string {
  const keys = useRef(new Map<string, string>());
  return useCallback((body: unknown): string => {
    const serialised = JSON.stringify(body);
    let key = keys.current.get(serialised);
    if (!key) {
      key = crypto.randomUUID();
      keys.current.set(serialised, key);
    }
    return key;
  }, []);
}
