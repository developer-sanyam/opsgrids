/**
 * Signing a page in without an auth service.
 *
 * `@opsgrids/client` (and the operator console's own copy of it) keeps the access token in memory
 * and mirrors it to `sessionStorage` under the app's own key, so a reload in the same tab stays
 * signed in: at boot, `AuthProvider` finds the token there and asks `GET /auth/me` who it belongs
 * to. The harness enters through exactly that door — it puts a token in `sessionStorage` before
 * the app's first script runs, and `/auth/me` (and `/members/me/grants`) are ordinary mocked
 * routes. That is the same seam the apps' own tests stub (`authApi.me`), one layer further out.
 *
 * The token is an unsigned JWT built from claims here, never a literal in the repository: nothing
 * in a browser verifies its signature (the gateway does), and the client only decodes its claims
 * to decide what to render.
 */

import type { Page } from "@playwright/test";

import { FIXED_NOW } from "./env";
import type { Theme } from "./env";

function base64url(value: unknown): string {
  return Buffer.from(JSON.stringify(value)).toString("base64url");
}

/** Claims shaped like `mint_access`'s (services/auth/app/tokens.py), expiring an hour after "now". */
export function unsignedToken(claims: Record<string, unknown>): string {
  const now = Math.floor(Date.parse(FIXED_NOW) / 1000);
  const payload = { iat: now, exp: now + 3600, ...claims };
  return `${base64url({ alg: "none", typ: "JWT" })}.${base64url(payload)}.visual-harness`;
}

export interface SessionSlot {
  /** The `sessionStorage` key the app's session module reads. */
  tokenKey: string;
  /** The `localStorage` key the app's theme script and provider read. */
  themeKey: string;
}

/** Before any of the page's own scripts run: the token, and the theme under test. */
export async function signIn(page: Page, slot: SessionSlot, token: string, theme: Theme): Promise<void> {
  await page.addInitScript(
    ({ tokenKey, themeKey, token: value, theme: palette }) => {
      window.sessionStorage.setItem(tokenKey, value);
      window.localStorage.setItem(themeKey, palette);
    },
    { ...slot, token, theme },
  );
}
