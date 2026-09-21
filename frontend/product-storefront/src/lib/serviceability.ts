/**
 * The buyer's serviceability check — validation, storage, wording and the request state machine.
 *
 * Everything here is deliberately outside `theme/render/`: the section component is then a thin
 * piece of markup over decisions that are testable without a DOM, and the copy is pinned in one
 * place rather than scattered through JSX.
 *
 * **The governing rule, from ADR-0010 §13, and the reason this module exists at all:**
 *
 * > A check that fails renders as "we couldn't check", never as "we don't deliver here". Unknown
 * > is not no.
 *
 * A naive `catch → not serviceable` tells a buyer standing inside a served area that the shop does
 * not reach them, and a Redis outage is enough to cause it (the gateway's rate limiter fails
 * closed, so a public check 503s wholesale). That buyer does not come back. So every failure path
 * below — transport, timeout, a 4xx, a 5xx, a body this build cannot interpret — lands on
 * `unknown`, and only a `serviceable: false` the server actually computed lands on `unserved`.
 *
 * **The shape is `ServiceabilityOut` in `libs/contracts/openapi/storefront.json`**, and nothing
 * here may be read off ADR-0010's prose instead: the ADR prints no response body, and the first
 * version of this module guessed one — an `outcome` enum, a top-level `promise_date`, a top-level
 * `currency` — none of which the service has ever sent. `api/serviceability.contract.test.ts`
 * reads that schema file and is what stops it happening twice.
 *
 * **The server computes the date, this module never does.** A `DATE_OFFSET` promise arrives as a
 * resolved calendar date in the *store's* timezone (ADR-0010 §3, ADR-0000 "Also settled"). Nothing
 * here adds days, applies a cutoff, or hands an ISO date to a formatter that would re-read it in
 * the reader's own timezone — a buyer in Singapore looking at a Mumbai store must see the Mumbai
 * answer, and a date shifted by one day is a support ticket the merchant cannot diagnose.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { checkServiceability } from "@/api/publicClient";
import type { ServiceabilityOut, UnservedReason } from "@/api/publicClient";

export type { ServiceabilityOut, UnservedReason } from "@/api/publicClient";

/* ------------------------------- postal codes ------------------------------ */

/**
 * The storage and wire form of a postal code: upper-cased, non-alphanumerics stripped.
 *
 * The same normalisation the service applies before it looks the code up (ADR-0010 §2), so
 * "400 072" and "400-072" are one code on both sides rather than two rows in the demand counter.
 */
export function normalisePostalCode(raw: string): string {
  return raw.toUpperCase().replace(/[^0-9A-Z]/g, "");
}

/** India's six-digit form. One country, one rule — §2 declines to generalise it on one use case. */
const INDIA_PIN = /^[1-9][0-9]{5}$/;

/**
 * Both messages are one line at 390px on purpose. The field sits in a bar at the top of the page,
 * so an error that wraps to two lines pushes the whole store down as the buyer types — which is the
 * same layout shift the answer region is built to avoid, arriving through the other half of the
 * control.
 */
export const POSTAL_CODE_REQUIRED = "Enter a PIN code to check.";
export const POSTAL_CODE_MALFORMED = "Enter six digits, like 400072.";

/**
 * Client validation that **mirrors** the server's rule and never replaces it.
 *
 * Its whole job is to save a buyer a round trip and to keep a typo out of the demand counter. The
 * service re-validates and is the only authority; a code this returns `null` for is still refused
 * there if the rule differs.
 */
export function postalCodeError(raw: string): string | null {
  const code = normalisePostalCode(raw);
  if (code === "") return POSTAL_CODE_REQUIRED;
  return INDIA_PIN.test(code) ? null : POSTAL_CODE_MALFORMED;
}

/* --------------------------------- storage -------------------------------- */

/**
 * Where the buyer's own postal code is kept, per store (ADR-0010 §6).
 *
 * **The code is stored. The answer never is.** A cached "yes" that has since become "no" is worse
 * than no cache at all — zones change, and a merchant who shrinks one would keep delivering to
 * every browser that had already asked. So every load re-checks, and the only thing storage buys
 * is that the buyer does not retype six digits on every page.
 */
export function postalCodeStorageKey(slug: string): string {
  return `opsgrids.storefront.${slug}.postal_code`;
}

/**
 * The stored code, or `null`.
 *
 * Validated on the way out as well as in: storage is shared with anything else on the origin and
 * is trivially editable, so a value that is not a postal code is discarded rather than posted.
 */
export function readStoredPostalCode(slug: string): string | null {
  if (slug === "") return null;
  let raw: string | null = null;
  try {
    raw = window.localStorage.getItem(postalCodeStorageKey(slug));
  } catch {
    /* Storage can be blocked outright — Safari's private mode, an embedded webview, a buyer who
       turned it off. Nothing breaks: they retype the code, which is the whole cost. */
    return null;
  }
  if (raw === null) return null;
  return postalCodeError(raw) === null ? normalisePostalCode(raw) : null;
}

export function writeStoredPostalCode(slug: string, code: string): void {
  if (slug === "") return;
  try {
    window.localStorage.setItem(postalCodeStorageKey(slug), normalisePostalCode(code));
  } catch {
    /* Over quota or blocked. The check still works for this page view. */
  }
}

/* --------------------------------- wording -------------------------------- */

/**
 * A duration, in words a buyer reads rather than a number they convert.
 *
 * Full words — "1 hour 30 minutes" — not the card's clipped "1 hr 30 mins": this is the store's
 * promise sentence, not a chip in the corner of a photograph.
 */
export function durationWords(minutes: number): string | null {
  if (!Number.isFinite(minutes) || minutes <= 0) return null;
  const whole = Math.round(minutes);
  const hours = Math.floor(whole / 60);
  const rest = whole % 60;
  const hourPart = hours === 1 ? "1 hour" : `${hours} hours`;
  const minutePart = rest === 1 ? "1 minute" : `${rest} minutes`;
  if (hours === 0) return minutePart;
  if (rest === 0) return hourPart;
  return `${hourPart} ${minutePart}`;
}

const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"] as const;
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"] as const;

const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})$/;

/**
 * "Wed 23 Sep", from the `YYYY-MM-DD` the server resolved in the **store's** timezone.
 *
 * Three things this deliberately does not do, each of which is a real defect somebody writes:
 *
 * - **It does no date arithmetic.** No offset is applied, no cutoff is evaluated, nothing is added
 *   to "today". The server already did that against the storefront's own timezone and clock; a
 *   second opinion computed from the buyer's device clock would disagree with the order they
 *   eventually place.
 * - **It never puts the date through the browser's timezone.** `new Date("2026-09-23")` is UTC
 *   midnight, and `toLocaleDateString()` west of Greenwich renders that as the 22nd. The parts are
 *   read out of the string and the weekday is taken from a UTC calendar date built from those same
 *   parts, so the output is identical in every timezone a buyer might be in.
 * - **It never invents a date it cannot read.** A string that is not `YYYY-MM-DD` is returned
 *   unchanged: showing the server's own value is honest, guessing at it is not.
 */
export function formatPromiseDate(iso: string): string {
  const parts = ISO_DATE.exec(iso);
  if (parts === null) return iso;
  const year = Number(parts[1]);
  const month = Number(parts[2]);
  const day = Number(parts[3]);
  const monthName = MONTHS[month - 1];
  if (monthName === undefined || day < 1 || day > 31) return iso;
  /* A UTC calendar date built from the parts, read back in UTC. This is a weekday lookup for a
     date that is already decided — no shifting, and the same answer at UTC−11 and at UTC+14. */
  const weekday = WEEKDAYS[new Date(Date.UTC(year, month - 1, day)).getUTCDay()];
  if (weekday === undefined) return iso;
  return `${weekday} ${day} ${monthName}`;
}

/* -------------------------------- outcomes -------------------------------- */

/**
 * The buyer-visible reading of a server answer.
 *
 * `headline` is the sentence the bar leads with; `detail` qualifies it. Kept here rather than in
 * the component so the copy is pinned by a test and cannot drift into a paraphrase.
 */
export interface ServedOutcome {
  kind: "served";
  /** The promise, or `null` for a slotted zone, where the window is chosen at checkout. */
  promise: string | null;
  /** What kind of served this is. `SLOTTED` is the one that has a window to pick rather than a
   *  promise to print — the second question, asked only once `serviceable` has answered the first. */
  mode: ServiceabilityOut["mode"];
  postalCode: string;
  answer: ServiceabilityOut;
}

export interface UnservedOutcome {
  kind: "unserved";
  postalCode: string;
  /** Which of the three refusals, or `null` for one this build has never heard of. */
  reason: UnservedReason | null;
  answer: ServiceabilityOut;
}

/**
 * The three refusals, in the buyer's words.
 *
 * `mode` is `UNSERVED` for all three, so this is the only thing that can tell them apart — and
 * they are genuinely different sentences. "Not mapped yet" is a promise to expand and the check
 * itself is the demand signal that decides where (§7). "Excluded" is a decision the merchant has
 * already made, and telling that buyer "we'll let you know when we get there" is a promise nobody
 * intends to keep. "Inactive" is the only temporary one. A zone's own `buyer_note` still wins over
 * all three — the merchant's words about their own area beat ours.
 */
export const UNSERVED_NOTE = "We've recorded the request — we'll let you know when we get there.";
export const UNSERVED_NOTE_EXCLUDED = "That's outside the area this store covers.";
export const UNSERVED_NOTE_PAUSED = "Deliveries to that area are paused for now.";

export const UNKNOWN_HEADLINE = "We couldn't check right now";
/** Two lines at 390px, like the other two details, so the bar is one height in every state. */
export const UNKNOWN_NOTE = "That's a connection problem, not a no. Try again in a moment.";
export const RETRY_LABEL = "Try again";

export function servedHeadline(postalCode: string): string {
  return `We deliver to ${postalCode}`;
}

/**
 * The refusal headline. **"yet" is a claim, so it is only made where it is true.**
 *
 * An unmapped code is somewhere the store has not reached; an excluded one is somewhere it has
 * decided not to go, and "yet" there tells a buyer to keep coming back. An unrecognised reason
 * takes the softest of the three, which is the one that promises the buyer least about *why*.
 */
export function unservedHeadline(postalCode: string, reason: UnservedReason | null): string {
  if (reason === "excluded") return `We don't deliver to ${postalCode}`;
  if (reason === "zone_inactive") return `We don't deliver to ${postalCode} right now`;
  return `We don't deliver to ${postalCode} yet`;
}

/** The sentence under the refusal headline, when the zone carries no `buyer_note` of its own. */
export function unservedNote(reason: UnservedReason | null): string {
  if (reason === "excluded") return UNSERVED_NOTE_EXCLUDED;
  if (reason === "zone_inactive") return UNSERVED_NOTE_PAUSED;
  return UNSERVED_NOTE;
}

/** The three the schema names. A fourth from a newer service reads as `null` and takes the
 *  general wording, which is true of every refusal, rather than being printed at a buyer raw. */
const UNSERVED_REASONS: readonly string[] = ["no_mapping", "excluded", "zone_inactive"];

export function knownReason(reason: string | null | undefined): UnservedReason | null {
  return reason != null && UNSERVED_REASONS.includes(reason) ? (reason as UnservedReason) : null;
}

/**
 * The promise sentence for a served answer, or `null` when there is nothing honest to say.
 *
 * `null` is a real outcome, not a gap to paper over: a slotted zone has no single promise (the
 * window is picked at checkout), and a `PROMISED` zone whose promise fields are missing is a
 * server that told us less than it should have. Both render the plain "we deliver here" and no
 * invented estimate — the announcement bar's merchant-typed `promiseText` stays the fallback.
 */
export function promiseLine(answer: ServiceabilityOut): string | null {
  /* `promise` is a nested object and is neither `required` nor non-null in the schema, so its
     absence is an ordinary answer rather than a fault. */
  const promise = answer.promise ?? null;
  if (promise === null) return null;
  if (promise.kind === "DURATION") {
    const minutes = promise.minutes ?? null;
    const words = minutes === null ? null : durationWords(minutes);
    return words === null ? null : `Delivery in ${words}`;
  }
  if (promise.kind === "DATE_OFFSET") {
    const date = promise.date ?? null;
    if (date === null || date === "") return null;
    /* The server's own resolved calendar date, rendered. Nothing is added to it here. */
    return `Arrives ${formatPromiseDate(date)}`;
  }
  /* Unreachable against today's schema, and deliberately kept: a third `kind` from a newer service
     is a promise this build cannot word, not an excuse to invent one. */
  return null;
}

/* ----------------------------- the state machine --------------------------- */

export type ServiceabilityState =
  /** Nothing asked yet. The bar shows the merchant's own store-wide promise underneath. */
  | { kind: "idle" }
  | { kind: "checking"; postalCode: string }
  | ServedOutcome
  | UnservedOutcome
  /** A failure of any kind. Never "no". */
  | { kind: "unknown"; postalCode: string };

/**
 * Turn a server answer into a buyer-visible outcome.
 *
 * **`serviceable` decides, and `mode` does not.** `mode` is required and never null — an unmapped
 * postal code, a deliberately excluded one and a switched-off zone all come back `UNSERVED`, which
 * is precisely why it cannot be the classifier: it cannot tell "we haven't reached your area yet"
 * from "we decided not to go there", and those are different sentences to put in front of a buyer
 * (ADR-0010 §3 keeps them apart on purpose). So the boolean answers the first question — do we
 * deliver — `reason` answers *why not*, and `mode` answers what kind of served this is.
 *
 * **Anything this build cannot interpret becomes `unknown`.** A body whose `serviceable` is not a
 * boolean is not an answer: a newer or broken service is a client that is out of date, not a shop
 * that does not deliver, and the cost of guessing in the two directions is not symmetric. An
 * unrecognised `mode` or `reason` is *not* in that class — the verdict was still computed, so it
 * is rendered, with the wording that holds for every refusal.
 */
export function classifyAnswer(
  answer: ServiceabilityOut,
  postalCode: string,
): ServedOutcome | UnservedOutcome | { kind: "unknown"; postalCode: string } {
  /* The server echoes the code it actually matched; fall back to what was typed so the sentence is
     never about an empty string. */
  const code = answer.postal_code === "" ? postalCode : answer.postal_code;
  /* A runtime check on a field the type already promises, because the type is a promise about a
     body this code did not build: `checkServiceability` casts what came off the wire. */
  if (typeof answer.serviceable !== "boolean") return { kind: "unknown", postalCode: code };
  if (answer.serviceable) {
    return { kind: "served", promise: promiseLine(answer), mode: answer.mode, postalCode: code, answer };
  }
  return { kind: "unserved", postalCode: code, reason: knownReason(answer.reason), answer };
}

export interface ServiceabilityController {
  state: ServiceabilityState;
  /** The postal code the last check was made against, whatever its outcome. */
  lastChecked: string | null;
  /** Validates, then asks the server. A code that fails validation never reaches the network. */
  check: (raw: string) => void;
  /** Re-asks with the last code. The "couldn't check" state's whole point. */
  retry: () => void;
}

/**
 * The check, as a hook.
 *
 * - **One request wins.** A buyer who retypes mid-flight gets the answer to what they last typed;
 *   an earlier reply that lands later is dropped, not painted over the newer one.
 * - **Never leaves a spinner up.** Every rejection resolves to `unknown`, which is a state with a
 *   button on it, so the editor canvas and the gallery miniature settle with no backend at all.
 *
 * **It does not check anything by itself.** The re-check-on-load that §5 requires belongs to the
 * caller, because only the caller can tell whether it is in front of a buyer: the gallery renders a
 * miniature of every theme, and a hook that asked on mount would post one check per miniature per
 * page view — each one landing in the merchant's own demand report as a postal code nobody typed.
 */
export function useServiceability(slug: string): ServiceabilityController {
  const [state, setState] = useState<ServiceabilityState>({ kind: "idle" });
  const [lastChecked, setLastChecked] = useState<string | null>(null);
  /* Monotonic, so a reply is matched to the request that asked for it. A ref rather than state:
     it must not schedule a render, and it must be readable by a closure that has already run. */
  const requestId = useRef(0);
  const live = useRef(true);

  useEffect(() => {
    live.current = true;
    return () => {
      live.current = false;
    };
  }, []);

  const run = useCallback(
    (code: string) => {
      requestId.current += 1;
      const mine = requestId.current;
      setLastChecked(code);
      setState({ kind: "checking", postalCode: code });
      void checkServiceability({ store: slug, postal_code: code })
        .then((answer) => {
          if (!live.current || mine !== requestId.current) return;
          setState(classifyAnswer(answer, code));
        })
        .catch(() => {
          /* Deliberately undiscriminating. A 404 store, a 429, a 503, a dead network and a body
             that would not parse are one thing to a buyer — we do not know — and they share one
             remedy, which is the button. The one thing none of them is, is "no". */
          if (!live.current || mine !== requestId.current) return;
          setState({ kind: "unknown", postalCode: code });
        });
    },
    [slug],
  );

  const check = useCallback(
    (raw: string) => {
      if (postalCodeError(raw) !== null) return;
      const code = normalisePostalCode(raw);
      writeStoredPostalCode(slug, code);
      run(code);
    },
    [run, slug],
  );

  const retry = useCallback(() => {
    if (lastChecked === null) return;
    run(lastChecked);
  }, [lastChecked, run]);

  return { state, lastChecked, check, retry };
}
