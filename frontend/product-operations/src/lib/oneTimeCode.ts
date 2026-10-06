/**
 * Some routes need a session that signed in with a one-time code — the put-away location reveal,
 * and connecting, changing, checking or disconnecting the organization's payment account (where a
 * business's money goes). The server answers a password-only or single-sign-on session with a 403
 * whose detail is `Additional authentication required: otp` (`require(..., amr={"otp"})` in
 * `platform_core/auth.py`). That problem's `type` is the generic `forbidden`, so the detail is the
 * only thing that tells it apart from a plain missing permission; every other 403 keeps the
 * server's own sentence.
 */

import { ApiError } from "@opsgrids/client";

export function needsOneTimeCodeSession(error: unknown): boolean {
  if (!(error instanceof ApiError) || error.status !== 403) return false;
  const detail = error.problem?.detail ?? "";
  return detail.startsWith("Additional authentication required:") && /\botp\b/.test(detail);
}
