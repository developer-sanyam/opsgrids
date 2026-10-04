import { useRef } from "react";

/**
 * One `Idempotency-Key` per distinct submission made from a dialog.
 *
 * The same request body gets the same key, so a double click, or a retry after a dropped response
 * or a 5xx, replays the first result on the server instead of doing the work twice. A corrected
 * value is a different body and therefore a fresh key. Mount the dialog only while it is open and
 * the keys are discarded with it: a later, deliberate repeat of the same action is a new request.
 */
export function useIdempotencyKeys(): (input: unknown) => string {
  const keys = useRef(new Map<string, string>());
  return (input) => {
    const body = JSON.stringify(input);
    let key = keys.current.get(body);
    if (!key) {
      key = crypto.randomUUID();
      keys.current.set(body, key);
    }
    return key;
  };
}
