/**
 * Every sentence the buyer surface says, in one file.
 *
 * Pinned here rather than scattered through JSX for the same reason `delivery/copy.ts` is: these
 * are the words a stranger reads on a merchant's own domain, with no console around them and no
 * support channel in reach. They are also what the tests assert against, so a paraphrase is a
 * failing test rather than a quiet change of meaning.
 *
 * **The two failure sentences are not interchangeable, and the difference is the point.** One
 * says we looked; the other says we could not look. Only the second offers a retry, because
 * retrying an answer cannot change it.
 */

/** While the bootstrap call is in flight. Announced, not drawn — the skeleton is the visual. */
export const LOADING_STATUS = "Loading this shop";

/** HTTP 404: we looked, and there is nothing published here. **No retry is offered.** */
export const NOT_FOUND_TITLE = "There's no shop at this address";
export const NOT_FOUND_BODY =
  "This web address isn't connected to a shop. If you typed it in, check the spelling — otherwise the shop may have moved.";

/** Everything that is not a 404: we could not look. A retry is the correct affordance. */
export const UNAVAILABLE_TITLE = "We can't load this shop right now";
export const UNAVAILABLE_BODY =
  "Something went wrong at our end, not yours. Try again in a moment.";
export const UNAVAILABLE_OFFLINE_BODY =
  "You appear to be offline. Reconnect and try again — nothing was lost.";
export const RETRY_LABEL = "Try again";

/**
 * The shop's own empty state.
 *
 * The address resolves, the shop is published, and there are no pages to draw: the public
 * contract carries the brand and nothing else yet, so there is genuinely nothing on this page.
 * Said plainly, from the buyer's side, without blaming the merchant or promising a date.
 */
export const NO_PAGES_TITLE = "There's nothing to see here yet";
export const NO_PAGES_BODY =
  "This shop is set up and this address works. Its pages will appear here once they're published.";

/**
 * Shown when the server says this is not the shop's main address (`is_canonical: false`).
 *
 * A quiet line, not a redirect and not a banner with a button: slice 1 deliberately does not
 * navigate, and pushing a buyer at the other host would be exactly wrong while a custom domain is
 * failing — which is one of the states that produces this flag.
 */
export function otherAddressNotice(canonicalHost: string): string {
  return `This shop's main address is ${canonicalHost}.`;
}
