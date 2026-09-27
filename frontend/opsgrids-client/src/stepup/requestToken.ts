/**
 * Ask the auth service for a step-up token — and tell a session token that lapsed under the dialog
 * from one that is still good.
 *
 * `authApi.stepUp` is bearer-authenticated with the session token. A **wrong code is a 422** (a
 * 401 would make the shared client refresh and re-send it, and count it twice), so the only 401
 * this call gets is about the bearer: the gateway turned an expired session token away, or the auth
 * service found the session ended. Left alone, a person whose laptop slept past the session
 * token's lifetime would be told "that code didn't work" for every correct code they typed, until
 * something else happened to refresh the session.
 *
 * So a 401 is checked once against the session itself: `me()` goes through `apiRequest`'s normal
 * path, which renews an expired token from the refresh cookie (or signs the person out, correctly,
 * when the session really is over). If that renewed the session token, the gateway turned the
 * first attempt away before the auth service ever saw the code — nothing was counted against the
 * lockout — and it is sent again, once. If the token did not change, the 401 stands and is thrown
 * as it came.
 */

import * as authApi from "../auth/api";
import { StepUpError } from "../auth/stepUpError";
import type { StepUpToken } from "../auth/types";
import { getAccessToken } from "../session";

async function sessionWasRenewed(): Promise<boolean> {
  const before = getAccessToken();
  try {
    await authApi.me();
  } catch {
    return false;
  }
  return getAccessToken() !== before;
}

export async function requestStepUpToken(code: string): Promise<StepUpToken> {
  try {
    return await authApi.stepUp(code);
  } catch (error) {
    if (!(error instanceof StepUpError) || error.status !== 401) throw error;
    if (!(await sessionWasRenewed())) throw error;
    return authApi.stepUp(code);
  }
}
