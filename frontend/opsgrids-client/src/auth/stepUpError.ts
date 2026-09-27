/**
 * The ways `POST /auth/mfa/step-up` refuses, as one typed error.
 *
 * The screen never branches on a status code or on the server's wording: it asks `failure`. Each
 * kind is a different thing to put in front of the person:
 *
 * - `wrong-code`: 401 or 422 — wrong, expired or already used. The code is single-use, so the
 *   answer is "wait for the next one", not "try the same digits again".
 * - `locked`: too many failed attempts. Nothing to fix by retyping; say so and let them wait.
 * - `not-enrolled`: 409 `mfa-not-enrolled` — the member has no second factor at all. No code can
 *   work; the way forward is the dashboard's Security page.
 * - `sso`: 403 `mfa-unavailable-for-sso` — the session came from single sign-on, which has no
 *   second factor of ours to check. Not a lock: nothing the person does with a code changes it.
 * - `reauthenticate`: 403 `mfa-reauthentication-required` — the authenticator was added after
 *   this session began (and not through it). No code can work until they sign in again.
 * - `unavailable`: everything else — a dropped connection, a timeout, a 5xx, an answer that is
 *   not a token. Says nothing about the code.
 *
 * The problem `type` slug decides first and the status only when the slug is one this file does
 * not know: a 403 is a lockout for `mfa-locked` and a refusal to even try for
 * `mfa-unavailable-for-sso`, and only the slug tells them apart. The slugs are the auth service's
 * contract (ADR-0023, "Error contract"). The mapping is the only place these are told apart, so a
 * change on the auth service is a change to `classifyStepUpFailure` and its test, and nothing else.
 */

import { ApiError } from "../http";
import type { ProblemJson } from "../http";
import { problemSlug } from "../problem";

export type StepUpFailure =
  | "wrong-code"
  | "locked"
  | "not-enrolled"
  | "sso"
  | "reauthenticate"
  | "unavailable";

/**
 * What each slug the auth service raises around a second factor means to a person at the code
 * prompt.
 *
 * `mfa-current-code-required` and `password-incorrect` belong to *adding* an authenticator
 * (`POST /auth/mfa/enrol`), which this prompt never calls. Were one to arrive here it would be a
 * contract violation rather than a wrong code, so it is typed `unavailable` — both are 422, which
 * would otherwise read as "retype the digits" when the digits were never the problem.
 * `password-locked` is sign-in's own lockout, which the same counters feed.
 */
const SLUG_FAILURES: Readonly<Record<string, StepUpFailure>> = {
  "mfa-not-enrolled": "not-enrolled",
  "mfa-unavailable-for-sso": "sso",
  "mfa-reauthentication-required": "reauthenticate",
  "mfa-locked": "locked",
  "password-locked": "locked",
  "mfa-code-invalid": "wrong-code",
  "mfa-code-replayed": "wrong-code",
  "mfa-current-code-required": "unavailable",
  "password-incorrect": "unavailable",
  "mfa-reseal-pending": "unavailable",
};

/**
 * A lockout may arrive as 403 (how sign-in's own MFA lockout answers), 423 or 429, or as any
 * status whose problem type says so. Every one of them means the same thing to the person.
 */
const LOCKED_STATUSES: ReadonlySet<number> = new Set([403, 423, 429]);

export function classifyStepUpFailure(status: number, problem: ProblemJson | null): StepUpFailure {
  const slug = problemSlug(problem);
  const known = SLUG_FAILURES[slug];
  if (known !== undefined) return known;
  if (status === 409) return "unavailable";
  if (LOCKED_STATUSES.has(status) || slug.includes("lock")) return "locked";
  if (status === 401 || status === 422) return "wrong-code";
  return "unavailable";
}

export class StepUpError extends Error {
  readonly failure: StepUpFailure;
  /** The HTTP status, or `null` when there was no response (offline, timed out, aborted). */
  readonly status: number | null;
  readonly problem: ProblemJson | null;

  constructor(
    failure: StepUpFailure,
    message: string,
    status: number | null,
    problem: ProblemJson | null,
    cause?: unknown,
  ) {
    super(message, cause === undefined ? undefined : { cause });
    this.name = "StepUpError";
    this.failure = failure;
    this.status = status;
    this.problem = problem;
  }
}

/** Whatever the request threw, as a `StepUpError`. */
export function toStepUpError(error: unknown): StepUpError {
  if (error instanceof StepUpError) return error;
  if (error instanceof ApiError) {
    return new StepUpError(
      classifyStepUpFailure(error.status, error.problem),
      error.message,
      error.status,
      error.problem,
      error,
    );
  }
  return new StepUpError("unavailable", "The step-up request did not complete.", null, null, error);
}
