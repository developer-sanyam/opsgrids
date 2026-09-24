/**
 * Telling the operator sign-in failures apart.
 *
 * They are different facts with different next steps, and the server already answers each with a
 * different status — so the screens key on the status (and, for the reseal case, the problem
 * `type`), never on the sentence. The sentence itself is always shown verbatim; it is what the
 * server chose to say, and paraphrasing it is how "check your device's clock" quietly stops being
 * true the day the server's wording changes.
 */

import { ApiError } from "@/api/http";

/** The problem `type` suffix of `MfaResealPendingProblem` (`services/auth/app/services.py`). */
const RESEAL_PENDING = "mfa-reseal-pending";

/**
 * A factor this deployment refuses to read yet — a legacy-plaintext TOTP row awaiting the
 * deploy-time reseal — stopped a code that may well have been right.
 *
 * It is the platform's state that is wrong, not the operator's code: the server answers 503 rather
 * than 401 precisely so it is **not counted** toward the lockout. A recovery code still signs in.
 */
export function isResealPending(error: unknown): error is ApiError {
  return (
    error instanceof ApiError &&
    error.status === 503 &&
    (error.problem?.type?.endsWith(`/${RESEAL_PENDING}`) ?? false)
  );
}

/**
 * The account is locked after repeated failures, and the pre-auth session behind this screen has
 * been revoked with it (`services._register_operator_mfa_failure`). Retrying the same code is
 * pointless; the operator starts again once the lock lapses.
 */
export function isLocked(error: unknown): error is ApiError {
  return error instanceof ApiError && error.status === 403;
}

/**
 * The server rejected what was presented: a wrong or already-spent code, or a pre-auth session
 * that has lapsed. The pending sign-in survives a rejection (unlike a lock), so the field stays
 * and the same call can simply be retried.
 */
export function isRejected(error: unknown): error is ApiError {
  return error instanceof ApiError && error.status === 401;
}
