import { useState } from "react";

/**
 * One `Idempotency-Key` per distinct submission.
 *
 * The same request body gets the same key, so a double click or a retry after a lost answer is a
 * replay the server answers with the first result rather than a second transfer, reservation or
 * adjustment. A body the person has since changed is a new submission and gets a fresh key.
 * Held per dialog or form instance, so reopening it starts clean.
 */
export function createSubmissionKeys(): (body: unknown) => string {
  const keys = new Map<string, string>();
  return (body) => {
    const fingerprint = JSON.stringify(body);
    let key = keys.get(fingerprint);
    if (key === undefined) {
      key = crypto.randomUUID();
      keys.set(fingerprint, key);
    }
    return key;
  };
}

export function useSubmissionKeys(): (body: unknown) => string {
  const [keyFor] = useState(createSubmissionKeys);
  return keyFor;
}
