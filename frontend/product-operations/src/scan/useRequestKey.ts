import { useRef } from "react";

/**
 * One `Idempotency-Key` per distinct request a screen makes: a double tap or a retry after a
 * dropped response sends the same key and replays the move instead of posting it twice; a
 * corrected value is a different request and gets a new key.
 */
export function useRequestKey(): (request: unknown) => string {
  const keys = useRef(new Map<string, string>());
  return (request) => {
    const body = JSON.stringify(request);
    let key = keys.current.get(body);
    if (!key) {
      key = crypto.randomUUID();
      keys.current.set(body, key);
    }
    return key;
  };
}
