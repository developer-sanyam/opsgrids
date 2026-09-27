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
 * - `sso`: 409 for a single sign-on session, which has no second factor of ours to check.
 * - `unavailable`: everything else — a dropped connection, a timeout, a 5xx, an answer that is
 *   not a token. Says nothing about the code, so the field keeps it.
 *
 * The mapping is the only place these are told apart, so a change on the auth service is a change
 * to `classifyStepUpFailure` and its test, and nothing else.
 */

import { ApiError } from "../http";
import type { ProblemJson } from "../http";

export type StepUpFailure = "wrong-code" | "locked" | "not-enrolled" | "sso" | "unavailable";

/** The problem `type` slug for "this member has no second factor enrolled". */
const NOT_ENROLLED_SLUG = "mfa-not-enrolled";

/** The last segment of the problem's `type` URI (`https://errors.opsgrids.dev/mfa-not-enrolled`). */
function slugOf(problem: ProblemJson | null): string {
  const type = problem?.type;
  if (!type) return "";
  return type.split("/").filter(Boolean).pop()?.toLowerCase() ?? "";
}

/**
 * A lockout may arrive as 403 (how sign-in's own MFA lockout answers), 423 or 429, or as any
 * status whose problem type says so. Every one of them means the same thing to the person.
 */
const LOCKED_STATUSES: ReadonlySet<number> = new Set([403, 423, 429]);

export function classifyStepUpFailure(status: number, problem: ProblemJson | null): StepUpFailure {
  const slug = slugOf(problem);
  if (status === 409) {
    if (slug === NOT_ENROLLED_SLUG) return "not-enrolled";
    if (slug.includes("sso")) return "sso";
    return "unavailable";
  }
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
