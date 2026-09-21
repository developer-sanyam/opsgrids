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
