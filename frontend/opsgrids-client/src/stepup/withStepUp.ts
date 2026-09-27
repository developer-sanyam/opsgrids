/**
 * Run one dangerous call with a step-up token: the held one if it is still good, otherwise a
 * fresh one the person has just earned with a code.
 *
 * This is the whole policy, kept apart from React so it can be read (and tested) as a function:
 *
 * 1. A token is held and unexpired: call `run` with it. Revealing a phone and then an email is one
 *    prompt, not two.
 * 2. The server says no to it (401 — its clock, or a revoked session, disagrees with ours): forget
 *    it and fall through to a prompt. This is the one place a 401 is *expected*, and it must never
 *    reach the sign-out handler; that is what `revealRequest` guarantees for the call itself.
 * 3. No usable token: `askForToken` gets one. It rejects (`StepUpCancelledError`) when the person
 *    gives up, and that propagates — `run` is never called without a proof.
 * 4. A fresh token is used at once, whatever the safety margin does to how long it is *held*.
 *
 * `keep` (default true) leaves the token in the holder for the rest of its lifetime after `run`
 * settles. `keep: false` drops it the moment `run` settles, success or failure — for a reveal that
 * should always ask.
 *
 * Any error `run` throws other than that 401 on a held token is the caller's: a 403 for a missing
 * permission or a 404 for a record that is gone says nothing about the step-up and is not retried.
 */

import { ApiError } from "../http";
import type { StepUpToken } from "../auth/types";
import { clearStepUpToken, getStepUpToken, setStepUpToken } from "./holder";
import type { RevealHeaders } from "./reveal";

export interface StepUpRunOptions {
  /** Keep the token for its remaining lifetime for a second reveal. Default `true`. */
  keep?: boolean | undefined;
}

/** The server no longer honours the step-up token it was sent. */
function isLapsed(error: unknown): boolean {
  return error instanceof ApiError && error.status === 401;
}

async function callWith<T>(
  token: string,
  run: (headers: RevealHeaders) => Promise<T>,
  keep: boolean,
): Promise<T> {
  try {
    return await run({ Authorization: `Bearer ${token}` });
  } catch (error) {
    if (isLapsed(error)) clearStepUpToken();
    throw error;
  } finally {
    if (!keep) clearStepUpToken();
  }
}

export async function withStepUp<T>(
  run: (headers: RevealHeaders) => Promise<T>,
  askForToken: () => Promise<StepUpToken>,
  options: StepUpRunOptions = {},
): Promise<T> {
  const keep = options.keep ?? true;

  const held = getStepUpToken();
  if (held !== null) {
    try {
      return await callWith(held, run, keep);
    } catch (error) {
      if (!isLapsed(error)) throw error;
    }
  }

  const fresh = await askForToken();
  if (keep) setStepUpToken(fresh.access_token, fresh.expires_in);
  return callWith(fresh.access_token, run, keep);
}
