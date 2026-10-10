/**
 * One `Idempotency-Key` per submission attempt.
 *
 * A retry of the same request (after a timeout, a dropped connection, a 5xx) must carry the key
 * the first try carried, so the server replays the posting instead of doing it twice. A request
 * whose content changed — the operator corrected a figure — is a different request and gets a
 * fresh key. The request's own serialisation is what tells the two apart.
 */
export interface AttemptKeyring {
  current: { fingerprint: string; key: string } | null;
}

export function newAttemptKeyring(): AttemptKeyring {
  return { current: null };
}

export function attemptKey(keyring: AttemptKeyring, request: unknown): string {
  const fingerprint = JSON.stringify(request);
  if (keyring.current?.fingerprint === fingerprint) return keyring.current.key;
  const key = crypto.randomUUID();
  keyring.current = { fingerprint, key };
  return key;
}
