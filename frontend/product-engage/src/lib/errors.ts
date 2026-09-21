import { ApiError } from "@opsgrids/client";

/**
 * What a failed write says on screen.
 *
 * An `ApiError` carries the service's own sentence — `vaayu.example.com has not been verified
 * yet`, `535 5.7.8 Username and Password not accepted` — and it is shown verbatim. The service
 * knows which rule it refused and this screen does not, so a friendlier rewrite here would be a
 * guess printed over a diagnosis.
 *
 * Anything else is the browser failing to reach the gateway at all, where the thrown value is a
 * `TypeError: Failed to fetch` that names nothing a customer can act on. That is the one case
 * this replaces.
 */
export function failureMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  return "Couldn't reach the server. Nothing was saved — try again.";
}

/**
 * Whether a failed read means Engage is not active for this organization.
 *
 * The gateway answers every `/engage` route with a `402` when the token's entitlements do not
 * carry `engage` — a `402` rather than a `403` on purpose, because the caller's permissions are
 * fine and the subscription is not. That is a state to offer a way out of, not a failure to
 * report. It is matched on the status alone: the detail sentence is the gateway's wording and
 * may change, the status is the contract. Every other status, and a request that never reached
 * the gateway at all, stays an error.
 */
export function isNotActivated(error: unknown): boolean {
  return error instanceof ApiError && error.status === 402;
}
