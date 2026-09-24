/**
 * Telling the operator sign-in failures apart.
 *
 * They are different facts with different next steps, and the server already answers each with a
 * different status — so the screens key on the status and the problem `type`, never on the
 * sentence. The sentence (`problemMessage`) is shown verbatim; it is what the server chose to say.
 */

import { ApiError } from "@opsgrids/client";

/** The problem `type` suffix of `MfaResealPendingProblem` (`services/auth/app/services.py`). */
const RESEAL_PENDING = "mfa-reseal-pending";

/**
 * A factor this deployment refuses to read yet — a legacy plaintext TOTP row awaiting the
 * deploy-time reseal — stopped a code that may well have been right.
 *
 * It is the platform's state that is wrong, not the operator's code: the server answers 503 rather
 * than 401 precisely so it is **not counted** toward the lockout. A recovery code still signs in,
 * and the screen has to say so plainly, because "That code is not valid" would be a lie.
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
 * been revoked with it. Retrying the same code is pointless; the operator starts again once the
 * lock lapses.
 */
export function isLocked(error: unknown): error is ApiError {
  return error instanceof ApiError && error.status === 403;
}

/**
 * The server rejected what was presented: a wrong or already-used code (401 "That code is not
 * valid" — the pre-auth session survives and the field stays), or a session that has lapsed
 * (401 "Please start signing in again"). The screen shows the sentence beside the field and always
 * offers a way back to the start.
 */
export function isRejected(error: unknown): error is ApiError {
  return error instanceof ApiError && error.status === 401;
}
