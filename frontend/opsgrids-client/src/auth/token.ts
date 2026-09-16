/**
 * Reading the access token's own claims.
 *
 * **Unverified, and only ever used to decide what to render.** The signature is checked at the
 * gateway against JWKS; nothing in a browser can verify it meaningfully, because the browser is
 * the caller's machine and the token is the caller's to edit. Anything decoded here is a hint for
 * the interface — never an authorization decision, never a reason to trust a value the server has
 * not also checked.
 */

import { getAccessToken } from "../session";

/** The claims `mint_access` puts in a product token (`services/auth/app/tokens.py`). */
export interface AccessClaims {
  /** The product this token addresses. */
  aud?: string;
  /** Activated products — what `useEntitlement` reads. */
  ent?: string[];
  /** Role codes. Deliberately *not* used to derive permissions: see `PermissionProvider`. */
  roles?: string[];
  /** Location codes the caller's scope covers. */
  loc?: string[];
  /** The organization's permission version; the gateway invalidates on a mismatch. */
  pv?: number;
  /** The caller's employee record, when they have one. */
  sref?: string;
  act_org?: string;
  exp?: number;
}

function decodeSegment(segment: string): unknown {
  const padded = segment.replace(/-/g, "+").replace(/_/g, "/");
  const json = atob(padded.padEnd(padded.length + ((4 - (padded.length % 4)) % 4), "="));
  // The payload is UTF-8; `atob` yields Latin-1, so round-trip anything above ASCII.
  const text = decodeURIComponent(
    Array.from(json, (char) => `%${char.charCodeAt(0).toString(16).padStart(2, "0")}`).join(""),
  );
  return JSON.parse(text);
}

/** The current token's claims, or `null` when there is no token or it is not a readable JWT. */
export function decodeAccessToken(token: string | null = getAccessToken()): AccessClaims | null {
  if (!token) return null;
  const payload = token.split(".")[1];
  if (!payload) return null;
  try {
    const claims = decodeSegment(payload);
    return typeof claims === "object" && claims !== null ? (claims as AccessClaims) : null;
  } catch {
    // A token we cannot read is the same as no token, for rendering purposes.
    return null;
  }
}
