/**
 * The one door the console's requests leave through, and the guard that keeps the two token
 * populations apart.
 *
 * ADR-0003 §2 draws the operator boundary with audience and gateway: an operator token names the
 * `operator` audience and carries `stf`, a tenant token names a product, and neither is accepted
 * where the other belongs. The server enforces that. This module makes the *client* incapable of
 * causing the mistake, in both directions:
 *
 * - **A tenant token is never sent to an operator route.** Before an authenticated request goes
 *   out, the token held in the session slot must itself read as an operator token. One that does
 *   not — because something else wrote to the slot — is dropped and never put on the wire.
 * - **An operator token is never sent to a tenant route.** Only `/admin/…` and `/auth/operator/…`
 *   are reachable through here. That includes `/auth/login`, `/auth/me` and the tenant grants
 *   route, which admin-gateway's `/auth` prefix would otherwise forward.
 *
 * Reading a token's claims in a browser proves nothing about its signature, and this is not
 * trying to: it is a hint that stops a wrong-audience token being *sent*, one hop before the
 * gateway would have refused it anyway.
 */

import {
  ApiError,
  apiRequest,
  decodeAccessToken,
  getAccessToken,
  notifyUnauthorized,
} from "@opsgrids/client";
import type { RequestOptions } from "@opsgrids/client";

/** The audience `auth-service` mints operator tokens for, and the only one admin-gateway accepts. */
export const OPERATOR_AUDIENCE = "operator";

/** What an operator sees when the session slot holds nothing usable — the sign-in screen follows. */
export const SESSION_NOT_VALID = "Your operator session is not valid. Sign in again.";

const OPERATOR_PREFIX = /^\/(?:admin|auth\/operator)\//;

function decodes(segment: string): string | null {
  try {
    return decodeURIComponent(segment);
  } catch {
    return null;
  }
}

/**
 * Whether a path stays inside the operator surface. The prefix alone is not enough: a URL parser
 * resolves `/admin/../members/me/grants` to a tenant route, so any dot segment — written plainly
 * or percent-encoded — is refused, along with a backslash or an empty segment, which some parsers
 * treat as a separator too.
 */
export function isOperatorPath(path: string): boolean {
  if (!OPERATOR_PREFIX.test(path)) return false;
  const pathname = path.split(/[?#]/, 1)[0] ?? "";
  if (pathname.includes("\\") || pathname.includes("//")) return false;
  return pathname.split("/").every((segment) => {
    const decoded = decodes(segment);
    return decoded !== null && decoded !== "." && decoded !== "..";
  });
}

/** Whether a token reads as an operator's: the operator audience, and the `stf` flag. */
export function isOperatorToken(token: string | null): boolean {
  const claims = decodeAccessToken(token);
  return claims !== null && claims.aud === OPERATOR_AUDIENCE && claims.stf === true;
}

/**
 * Issue a request to admin-gateway on the operator audience.
 *
 * `auth: false` is for the pre-auth sign-in calls, which carry their credential in the body. A
 * refused call rejects rather than throwing synchronously, so every caller handles one failure
 * shape.
 */
export async function operatorRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  if (!isOperatorPath(path)) {
    throw new Error(`The operator console only calls /admin and /auth/operator routes, not ${path}.`);
  }
  if (options.auth !== false && !isOperatorToken(getAccessToken())) {
    // Clearing the slot fires the unauthorized handler, which is what returns the operator to the
    // sign-in screen. Nothing has been sent.
    notifyUnauthorized();
    throw new ApiError(401, SESSION_NOT_VALID, null);
  }
  return apiRequest<T>(path, options);
}
