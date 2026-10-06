/**
 * A fresh random UUID (v4) for one put-away's `Idempotency-Key`.
 *
 * Minted once, at the moment the worker confirms, and stored with the request body: every resend
 * of that put-away — a retry, a replay after reconnecting, a replay after the app was closed —
 * presents the same key, which is the whole point of the key. `crypto.randomUUID` needs a secure
 * context; `getRandomValues` does not, so an older WebView still gets a properly random key.
 */
export function newIdempotencyKey(): string {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  // RFC 4122 §4.4: version 4 in the high nibble of byte 6, variant 10xx in byte 8.
  bytes[6] = ((bytes[6] ?? 0) & 0x0f) | 0x40;
  bytes[8] = ((bytes[8] ?? 0) & 0x3f) | 0x80;
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}
