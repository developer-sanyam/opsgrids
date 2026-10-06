import { problemMessage } from "@opsgrids/client";

/**
 * The provider's hosted checkout could not be loaded in this browser — its script was blocked or
 * never arrived. Unlike a dropped request, its message is written for the operator (what to check:
 * the connection, an ad blocker), so it is shown as it is rather than as "Something went wrong".
 */
export class CheckoutLoadError extends Error {
  override name = "CheckoutLoadError";
}

/** What to tell the operator about a failed collect: a checkout that would not load in its own
 *  words, anything else as the server's problem detail. */
export function checkoutErrorMessage(error: unknown): string {
  return error instanceof CheckoutLoadError ? error.message : problemMessage(error);
}
