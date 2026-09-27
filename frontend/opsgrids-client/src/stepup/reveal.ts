/**
 * How a reveal call carries the step-up token.
 *
 * The step-up token replaces the session token as the bearer of exactly one call — the dangerous
 * one. That call therefore goes through `apiRequest` with `auth: false` (so the session token is
 * not attached) and its own `Authorization` header, and with `signalUnauthorized: false`: a step-up
 * token that has lapsed answers 401, and the default handling would refresh the *session* and then
 * sign the person out over a token that was never the session. `revealRequest` writes those three
 * options once so no app has to remember them:
 *
 *     stepUp.run((headers) =>
 *       apiRequest<Unmasked>(`/contacts/${id}:reveal`, { method: "POST", body, ...revealRequest(headers) }),
 *     );
 */

/** The headers `run` hands to the reveal call: the step-up token as the bearer, nothing else. */
export type RevealHeaders = { readonly Authorization: string };

/** The part of `RequestOptions` that `revealRequest` fixes. Spread it into `apiRequest`'s options. */
export interface RevealRequestOptions {
  auth: false;
  signalUnauthorized: false;
  headers: RevealHeaders;
}

/** The `apiRequest` options that present `headers` as the call's only credential. */
export function revealRequest(headers: RevealHeaders): RevealRequestOptions {
  return { auth: false, signalUnauthorized: false, headers };
}
