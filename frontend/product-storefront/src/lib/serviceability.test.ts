/**
 * The serviceability module, without a DOM in the way.
 *
 * Everything pinned here is a decision the ADR makes and a later contributor could quietly undo:
 * that a malformed code is refused before the network, that the buyer's *answer* is never written
 * to storage, that a transport failure is not a refusal, and that a server-resolved date is
 * rendered rather than recomputed.
 */

import { afterEach, describe, expect, it } from "vitest";

import type { ServiceabilityOut, UnservedReason } from "@/api/publicClient";
import {
  POSTAL_CODE_MALFORMED,
  POSTAL_CODE_REQUIRED,
  UNSERVED_NOTE,
  UNSERVED_NOTE_EXCLUDED,
  classifyAnswer,
  durationWords,
  formatPromiseDate,
  normalisePostalCode,
  postalCodeError,
  postalCodeStorageKey,
  promiseLine,
  readStoredPostalCode,
  unservedHeadline,
  unservedNote,
  writeStoredPostalCode,
} from "./serviceability";

/** A served answer in the shape `libs/contracts/openapi/storefront.json` publishes. */
function answer(overrides: Partial<ServiceabilityOut> = {}): ServiceabilityOut {
  return {
    postal_code: "400050",
    serviceable: true,
    mode: "PROMISED",
    reason: null,
    promise: { kind: "DURATION", minutes: 25 },
    fees: {
      currency: "INR",
      delivery_fee_minor: 2900,
      free_delivery_above_minor: 49900,
      min_order_minor: 9900,
      display_only: true,
    },
    buyer_note: null,
    checked_at: "2026-09-19T09:15:00Z",
    ...overrides,
  };
}

/** A refusal. All three reasons share `mode: "UNSERVED"` — that is the point of the tests below. */
function refusal(reason: UnservedReason | null, postal_code = "422001"): ServiceabilityOut {
  return answer({
    postal_code,
    serviceable: false,
    mode: "UNSERVED",
    reason,
    promise: null,
    fees: null,
  });
}

describe("normalising a postal code", () => {
  it("upper-cases and strips everything that is not alphanumeric", () => {
    // The same normalisation the service applies, so "400 072" and "400-072" are one row in the
    // demand counter rather than three.
    expect(normalisePostalCode(" 400-072 ")).toBe("400072");
    expect(normalisePostalCode("sw1a 1aa")).toBe("SW1A1AA");
  });
});

describe("validating a postal code", () => {
  it("accepts India's six-digit form", () => {
    expect(postalCodeError("400072")).toBeNull();
    expect(postalCodeError("400 072")).toBeNull();
  });

  it("names the two ways it can be wrong differently", () => {
    expect(postalCodeError("")).toBe(POSTAL_CODE_REQUIRED);
    expect(postalCodeError("   ")).toBe(POSTAL_CODE_REQUIRED);
    expect(postalCodeError("4007")).toBe(POSTAL_CODE_MALFORMED);
    expect(postalCodeError("4000720")).toBe(POSTAL_CODE_MALFORMED);
    expect(postalCodeError("ABCDEF")).toBe(POSTAL_CODE_MALFORMED);
  });

  it("refuses a leading zero, which no Indian PIN code has", () => {
    expect(postalCodeError("040072")).toBe(POSTAL_CODE_MALFORMED);
  });
});

describe("what is kept in the browser", () => {
  afterEach(() => {
    window.localStorage.clear();
  });

  it("stores the postal code the buyer typed, and only that", () => {
    writeStoredPostalCode("greenleaf", "400 072");

    // ADR-0010 §6: the code is kept so a buyer does not retype it. The *answer* is not, because a
    // cached "yes" that has since become "no" is worse than no cache at all.
    expect(window.localStorage.getItem(postalCodeStorageKey("greenleaf"))).toBe("400072");
    expect(Object.keys(window.localStorage)).toEqual([postalCodeStorageKey("greenleaf")]);
  });

  it("keys by store, so two storefronts on one origin do not share an address", () => {
    writeStoredPostalCode("greenleaf", "400072");
    writeStoredPostalCode("quickcart", "400050");

    expect(readStoredPostalCode("greenleaf")).toBe("400072");
    expect(readStoredPostalCode("quickcart")).toBe("400050");
  });

  it("discards a stored value that is not a postal code rather than posting it", () => {
    // Storage is shared with everything else on the origin and is trivially editable.
    window.localStorage.setItem(postalCodeStorageKey("greenleaf"), "<script>");

    expect(readStoredPostalCode("greenleaf")).toBeNull();
  });

  it("reads and writes nothing at all without a store handle", () => {
    writeStoredPostalCode("", "400072");

    expect(readStoredPostalCode("")).toBeNull();
    expect(Object.keys(window.localStorage)).toEqual([]);
  });
});

describe("the promise, in words", () => {
  it("says a duration the way a person says it", () => {
    expect(durationWords(25)).toBe("25 minutes");
    expect(durationWords(1)).toBe("1 minute");
    expect(durationWords(60)).toBe("1 hour");
    expect(durationWords(95)).toBe("1 hour 35 minutes");
    expect(durationWords(120)).toBe("2 hours");
  });

  it("has nothing to say about a promise of no time at all", () => {
    expect(durationWords(0)).toBeNull();
    expect(durationWords(-5)).toBeNull();
    expect(durationWords(Number.NaN)).toBeNull();
  });

  it("renders a duration answer and a date answer from what the server sent", () => {
    expect(promiseLine(answer())).toBe("Delivery in 25 minutes");
    expect(promiseLine(answer({ promise: { kind: "DATE_OFFSET", date: "2026-09-23" } }))).toBe(
      "Arrives Wed 23 Sep",
    );
  });

  it("invents nothing when the server sent no promise to render", () => {
    // A slotted zone picks its window at checkout, and a `PROMISED` zone with no promise fields is
    // a server that told us less than it should have. Neither is an excuse to estimate.
    expect(promiseLine(answer({ mode: "SLOTTED", promise: null }))).toBeNull();
    expect(promiseLine(answer({ promise: { kind: "DURATION", minutes: null } }))).toBeNull();
    expect(promiseLine(answer({ promise: { kind: "DATE_OFFSET", date: null } }))).toBeNull();
  });

  it("reads the promise out of the nested object the schema publishes", () => {
    /*
     * The shape this feature got wrong once: there is no top-level `promise_date`. A build that
     * looked for one found `undefined` on every answer and printed no promise at all — the fee
     * line where the delivery time should be.
     */
    const dated = answer({ promise: { kind: "DATE_OFFSET", date: "2026-09-25" } });
    expect(Object.keys(dated)).not.toContain("promise_date");
    expect(promiseLine(dated)).toBe("Arrives Fri 25 Sep");
  });
});

describe("a server-supplied date", () => {
  const ORIGINAL_TZ = process.env.TZ;

  afterEach(() => {
    process.env.TZ = ORIGINAL_TZ;
  });

  it("is rendered, never recomputed — the same day in every timezone a buyer sits in", () => {
    /*
     * The defect this exists for: `new Date("2026-09-23")` is UTC midnight, so anywhere west of
     * Greenwich `toLocaleDateString()` prints the 22nd. The server resolved this date in the
     * *store's* timezone (ADR-0010 §3); a buyer in Singapore looking at a Mumbai store must see
     * the Mumbai answer, and a date off by one is a support ticket a merchant cannot diagnose.
     */
    const seen = new Set<string>();
    for (const zone of ["Pacific/Kiritimati", "Pacific/Niue", "Asia/Kolkata", "UTC"]) {
      process.env.TZ = zone;
      seen.add(formatPromiseDate("2026-09-23"));
    }

    expect([...seen]).toEqual(["Wed 23 Sep"]);
  });

  it("does no arithmetic on it — no offset, no cutoff, no rounding to a nearer day", () => {
    // Each of these is the exact calendar date the server named, including the two a "today + n"
    // implementation would have moved across a month and a year boundary.
    expect(formatPromiseDate("2026-09-30")).toBe("Wed 30 Sep");
    expect(formatPromiseDate("2026-10-01")).toBe("Thu 1 Oct");
    expect(formatPromiseDate("2026-12-31")).toBe("Thu 31 Dec");
    expect(formatPromiseDate("2027-01-01")).toBe("Fri 1 Jan");
    expect(formatPromiseDate("2028-02-29")).toBe("Tue 29 Feb");
  });

  it("shows a value it cannot read rather than guessing at one", () => {
    expect(formatPromiseDate("next Tuesday")).toBe("next Tuesday");
    expect(formatPromiseDate("2026-13-01")).toBe("2026-13-01");
    expect(formatPromiseDate("")).toBe("");
  });
});

describe("classifying an answer", () => {
  it("reads served from `serviceable`, and the kind of served from `mode`", () => {
    const served = classifyAnswer(answer(), "400050");
    expect(served.kind).toBe("served");
    expect(served.kind === "served" ? served.promise : null).toBe("Delivery in 25 minutes");

    const slotted = classifyAnswer(answer({ mode: "SLOTTED", promise: null }), "400050");
    expect(slotted.kind).toBe("served");
    expect(slotted.kind === "served" ? slotted.promise : "unset").toBeNull();
    expect(slotted.kind === "served" ? slotted.mode : null).toBe("SLOTTED");
  });

  it("calls an unmapped postal code not served, though its mode says only `UNSERVED`", () => {
    /*
     * The commonest refusal of all, and the reason `mode` is not the classifier: it comes back
     * `UNSERVED` here, `UNSERVED` for a deliberately excluded code and `UNSERVED` for a switched-off
     * zone. The verdict is `serviceable`; `mode` cannot answer the question at all.
     */
    const out = classifyAnswer(refusal("no_mapping"), "422001");

    expect(out.kind).toBe("unserved");
    expect(out.kind === "unserved" ? out.reason : null).toBe("no_mapping");
  });

  it("keeps the three refusals apart, which is the one thing `mode` cannot do", () => {
    const unmapped = refusal("no_mapping");
    const excluded = refusal("excluded", "194101");
    const inactive = refusal("zone_inactive", "590001");

    // Identical modes, three different facts (ADR-0010 §3).
    expect([unmapped.mode, excluded.mode, inactive.mode]).toEqual(["UNSERVED", "UNSERVED", "UNSERVED"]);

    const reasons = [unmapped, excluded, inactive].map((a) => {
      const out = classifyAnswer(a, a.postal_code);
      return out.kind === "unserved" ? out.reason : null;
    });
    expect(reasons).toEqual(["no_mapping", "excluded", "zone_inactive"]);

    // "yet" is a promise to expand, and it is only made where the store has not got there yet.
    expect(unservedHeadline("422001", "no_mapping")).toBe("We don't deliver to 422001 yet");
    expect(unservedHeadline("194101", "excluded")).toBe("We don't deliver to 194101");
    expect(unservedNote("no_mapping")).toBe(UNSERVED_NOTE);
    expect(unservedNote("excluded")).toBe(UNSERVED_NOTE_EXCLUDED);
  });

  it("still answers when the server sends only the four fields it must", () => {
    // `reason`, `promise`, `fees` and `buyer_note` are not in the schema's `required` list, so an
    // answer without them is ordinary. A read that assumed them would throw on a valid body.
    const bare: ServiceabilityOut = {
      postal_code: "400072",
      serviceable: true,
      mode: "PROMISED",
      checked_at: "2026-09-19T09:15:00Z",
    };

    const out = classifyAnswer(bare, "400072");

    expect(out.kind).toBe("served");
    expect(out.kind === "served" ? out.promise : "unset").toBeNull();
  });

  it("renders a refusal it has a reason for and one it does not, and never blanks either", () => {
    // A fourth reason from a newer service is still a refusal the server computed. It takes the
    // general wording rather than being printed raw at a buyer or losing its sentence.
    const out = classifyAnswer(
      refusal("port_closed" as unknown as UnservedReason),
      "422001",
    );

    expect(out.kind).toBe("unserved");
    expect(out.kind === "unserved" ? out.reason : "unset").toBeNull();
    expect(unservedNote(null)).toBe(UNSERVED_NOTE);
  });

  it("falls back to the code that was typed when the server echoes none", () => {
    const out = classifyAnswer(answer({ postal_code: "" }), "400050");
    expect(out.postalCode).toBe("400050");
  });

  it("treats an answer it cannot interpret as unknown, never as a refusal", () => {
    // A body whose verdict is not a boolean is not an answer. A newer or broken service is a client
    // that is out of date, not a shop that does not deliver, and the cost of guessing in the two
    // directions is not symmetric.
    const out = classifyAnswer(
      answer({ serviceable: "maybe" as unknown as boolean }),
      "400050",
    );

    expect(out.kind).toBe("unknown");
  });

  it("does not call an unrecognised mode unknown — the verdict was still computed", () => {
    /*
     * The other half of the same judgement. A mode this bundle has never heard of on an answer
     * that says `serviceable: true` is a shop that delivers and a wording this build cannot pick;
     * refusing to show the answer at all would be the opposite mistake to the one above.
     */
    const out = classifyAnswer(
      answer({ mode: "WINDOWED" as unknown as ServiceabilityOut["mode"], promise: null }),
      "400050",
    );

    expect(out.kind).toBe("served");
  });
});
