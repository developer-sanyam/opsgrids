/**
 * The one-per-line link convention, and the one place a link destination is judged.
 *
 * Two things live here because they are the same thing: a merchant types a destination into a
 * plain text field, and something has to decide both *how* to render it and *whether* to render
 * it as a link at all.
 *
 * `logo-wall` already established that a repeating list of short strings is a `textarea` with one
 * entry per line — settings are flat, so an array is not available and never will be. A footer
 * needs the same list with a destination attached, so the convention extends to
 * `Label | /href`.
 *
 * **Tolerant on purpose.** A merchant typing into a plain textarea will leave off the pipe, put
 * spaces around it, or paste a line that is not a link at all ("Mon to Sat, 10am to 7pm"). None of
 * those is an error worth showing them: a line with no pipe is label-only text, and a pipe with
 * nothing after it is the same thing. The one line that is dropped is an empty one, because a
 * blank list item is a hole in the layout rather than information.
 */

import type { Settings } from "./settings";
import { str } from "./settings";

/* -------------------------- where a link may point ------------------------- */

/**
 * The scheme allow-list. **One policy for every href in the section library** — `TileShell`,
 * `ActionLink` and every link column go through `classifyHref`, so there is nothing to keep in
 * sync and no second opinion to drift.
 *
 * **The threat model, because it decides the shape of this.** The merchant filling in these
 * settings is a trusted user editing their own store; this is not first and foremost "an attacker
 * injects script into someone else's page". It is worth closing anyway, for three reasons that do
 * not depend on the merchant being hostile:
 *
 * 1. A store document is *data*. Today it is authored in the editor beside this code; it will
 *    arrive over a wire from a service that does not exist yet, and a renderer that trusts its
 *    input only because of where that input happens to come from today is a renderer that becomes
 *    a hole the day that changes.
 * 2. Editor access is a permission. A store's owner can grant it to staff, and "staff member with
 *    edit rights" is not the same trust level as "account owner" — a `javascript:` href authored
 *    by one of them runs on the store's own origin, against the owner's own session.
 * 3. There is nothing to preserve. `javascript:`, `data:` and `vbscript:` in a storefront link are
 *    never a legitimate thing for a merchant to want, so refusing them costs no use case at all.
 *
 * `http:` is permitted where an iframe `src` in `VideoList` permits only `https:`. The asymmetry
 * is deliberate: a frame executes third-party script inside the store's own page, while a link
 * hands the visitor to their browser and leaves. Refusing `http:` outbound would break links to
 * the older supplier and municipal sites a trade merchant genuinely lists.
 */
const PERMITTED_SCHEMES = new Set(["https:", "http:", "mailto:", "tel:"]);

/** What the renderer should do with a destination a merchant typed. */
export type HrefDecision =
  /** A path inside this app. Goes through the router, so the preview does not reload the page. */
  | { kind: "internal"; href: string }
  /** A permitted absolute destination. A plain `<a>`; the router cannot take anyone there. */
  | { kind: "external"; href: string }
  /** Not a destination we will render a link to. The caller renders no anchor at all. */
  | { kind: "refused" };

const REFUSED = { kind: "refused" } as const;

/**
 * A C0 control character or a DEL anywhere in the value refuses it outright.
 *
 * Browsers strip tab, newline and carriage return out of a URL *before* dispatching it, so a
 * scheme with a tab wedged inside it is a live `javascript:` link that no `startsWith` and no
 * scheme pattern ever sees. Nothing a merchant legitimately types contains one, so the whole
 * class goes rather than being normalised away and then re-judged.
 *
 * Written as a scan rather than a character class because a regular expression carrying literal
 * control characters is itself a lint error — and the loop says what it means without anyone
 * having to decode an escape range.
 */
function hasControlCharacter(value: string): boolean {
  for (let i = 0; i < value.length; i += 1) {
    const code = value.charCodeAt(i);
    if (code <= 0x1f || code === 0x7f) return true;
  }
  return false;
}

/**
 * Judge one destination.
 *
 * Parsed with `new URL()` rather than matched with a pattern — the same tool `VideoList` uses on
 * an embed `src`, and for the same reason: the scheme is whatever the URL parser says it is, so a
 * mixed-case `JaVaScRiPt:`, a leading space and an embedded newline are all the parser's problem
 * and not a regular expression's.
 *
 * Permitted: `https:`, `http:`, `mailto:`, `tel:`, and a root-relative path. Everything else is
 * refused — `javascript:`, `data:`, `vbscript:`, `file:`, a protocol-relative `//host`, a bare
 * `#`, a relative `catalogue` with no leading slash, and any scheme not on the list.
 */
export function classifyHref(raw: string): HrefDecision {
  const value = raw.trim();
  if (value === "") return REFUSED;
  if (hasControlCharacter(value)) return noteRefusal(value);

  /* `//host` is protocol-relative — an absolute address wearing a path's clothes, and a way out of
     the store that names no scheme. A leading `/\` is the same thing: browsers read a backslash in
     the authority position as a slash, so it walks past a check that only knows about `//`. */
  if (/^[/\\]{2}/.test(value)) return noteRefusal(value);
  if (value.startsWith("/")) return { kind: "internal", href: value };

  let url: URL;
  try {
    url = new URL(value);
  } catch {
    // No scheme and no leading slash: a bare `#`, a relative path, or something that is not an
    // address at all. None of those is a destination this renderer will guess at.
    return noteRefusal(value);
  }
  if (!PERMITTED_SCHEMES.has(url.protocol)) return noteRefusal(value);

  /* A bare `mailto:` or `tel:` parses cleanly and carries a permitted scheme, and is still a link
     that goes nowhere — a merchant who typed the scheme and stopped. A hierarchical address always
     has at least `/` here, so only the empty opaque case is caught. */
  if (url.pathname === "") return noteRefusal(value);

  /* The value as typed, not `url.href`. It has already been proved to parse and to carry a
     permitted scheme, and re-serialising would quietly rewrite what the merchant entered — an
     Instagram address would go out with a trailing slash it was not given. */
  return { kind: "external", href: value };
}

/** Whether a destination will render as a link at all. */
export function isSafeHref(href: string): boolean {
  return classifyHref(href).kind !== "refused";
}

/**
 * Whether an href leaves the app, and so must be a plain `<a>` rather than a router `Link`.
 *
 * **A refused destination is not external.** It is not anything: the answer here is `false`, and a
 * caller that renders something either way must ask `classifyHref` instead, rather than reading
 * "not external" as "safe to hand to the router".
 */
export function isExternalHref(href: string): boolean {
  return classifyHref(href).kind === "external";
}

const warnedRefusals = new Set<string>();

/**
 * Say so once, in a dev build, when a destination is refused.
 *
 * The rendered fallback is what a merchant actually sees — their words stay on the page with no
 * link on them — but a line in the console names the value and the rule, which is the difference
 * between "my button stopped working" and a five-second fix. Deduplicated by value, because one
 * refused href in a rail of twelve cards would otherwise print twelve times per render.
 */
function noteRefusal(value: string): HrefDecision {
  if (import.meta.env.DEV && !warnedRefusals.has(value)) {
    warnedRefusals.add(value);
    console.warn(
      `Storefront: link destination refused, so no link was rendered — ${JSON.stringify(value)}. ` +
        'Use https:, http:, mailto:, tel:, or a path beginning with "/".',
    );
  }
  return REFUSED;
}

/* --------------------------------- the list -------------------------------- */

export interface LinkEntry {
  /** What the buyer reads. Never empty — a line with no label is dropped. */
  label: string;
  /**
   * Where it goes, or `null` for a line that is text and not a link.
   *
   * A destination the policy refuses arrives here as `null` too, which is what carries the policy
   * into every link column without each of them repeating the check: a column already renders a
   * `null` entry as plain text, so a refused line reads as the merchant's own words with no anchor
   * on them — never as a link that goes nowhere.
   */
  href: string | null;
}

/** Parse the whole textarea. Exported for its own test; sections go through `linkEntries`. */
export function parseLinkList(text: string): LinkEntry[] {
  const entries: LinkEntry[] = [];
  for (const raw of text.split("\n")) {
    const line = raw.trim();
    if (line === "") continue;

    // Split on the *first* pipe only: a label may legitimately contain one, a URL may not.
    const pipe = line.indexOf("|");
    if (pipe === -1) {
      entries.push({ label: line, href: null });
      continue;
    }

    const label = line.slice(0, pipe).trim();
    const href = line.slice(pipe + 1).trim();
    if (label === "") continue;
    const decision = classifyHref(href);
    entries.push({ label, href: decision.kind === "refused" ? null : decision.href });
  }
  return entries;
}

/** The settings-side reader, matching `lines()` in shape. */
export function linkEntries(settings: Settings, key: string): LinkEntry[] {
  return parseLinkList(str(settings, key));
}
