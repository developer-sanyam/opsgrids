import { describe, expect, it } from "vitest";

import { ApiError } from "@opsgrids/client";

import {
  completeFailureMessage,
  hasFault,
  metaFaultMessage,
  problemCode,
  signupFailureMessage,
} from "./failureMessages";

/** Every code `w1_setup` / `w1_services` can fail a run or a `signup/complete` with. */
const SERVER_CODES = [
  "code_expired",
  "code_exchange_failed",
  "meta_unreachable",
  "not_configured",
  "setup_in_progress",
  "waba_ambiguous",
  "waba_not_found",
  "waba_not_accessible",
  "no_phone_number",
  "number_not_found",
  "ownership_check_failed",
  "number_claimed",
  "webhook_subscribe_failed",
  "webhook_not_subscribed",
  "pin_required",
  "number_not_verified",
  "register_rate_limited",
  "register_failed",
  "number_not_connected",
  "templates_sync_failed",
  "verify_failed",
  "channel_not_connected",
  "token_missing",
  "timed_out",
  "abandoned",
  "internal_error",
];

describe("signupFailureMessage", () => {
  it("has its own sentence for every code the service can produce, and never echoes the code", () => {
    for (const code of SERVER_CODES) {
      const sentence = signupFailureMessage(code, "server detail");
      expect(sentence, code).not.toBe("server detail");
      expect(sentence, code).not.toContain(code);
      expect(sentence.length, code).toBeGreaterThan(20);
    }
  });

  it("no longer tells the customer to pick one when there is no picker", () => {
    expect(signupFailureMessage("waba_ambiguous")).not.toMatch(/pick one/i);
  });

  it("falls back to the service's sentence for a code it has never seen, then to a general line", () => {
    expect(signupFailureMessage("brand_new_code", "Meta said no.")).toBe("Meta said no.");
    expect(signupFailureMessage("brand_new_code", null)).toMatch(/Setup stopped/);
    expect(signupFailureMessage(null, undefined)).toMatch(/Setup stopped/);
    // A code that is an Object.prototype key is not a message.
    expect(signupFailureMessage("constructor", null)).toMatch(/Setup stopped/);
  });
});

describe("completeFailureMessage", () => {
  const coded = (code: string) => new ApiError(409, "raw sentence", { errors: [{ field: "", code, message: "" }] });

  it("prefers the friendly sentence for the problem's code", () => {
    expect(problemCode(coded("code_expired"))).toBe("code_expired");
    expect(completeFailureMessage(coded("code_expired"), "offline")).toMatch(/expired or was already used/);
  });

  it("shows the service's own sentence when the code is unknown or absent", () => {
    expect(completeFailureMessage(coded("other"), "offline")).toBe("raw sentence");
    expect(completeFailureMessage(new ApiError(400, "That code has expired.", null), "offline")).toBe(
      "That code has expired.",
    );
  });

  it("says the server could not be reached for anything that is not an API error", () => {
    expect(completeFailureMessage(new TypeError("Failed to fetch"), "offline")).toBe("offline");
    expect(problemCode(new TypeError("x"))).toBeNull();
  });
});

describe("metaFaultMessage", () => {
  it("appends the code and session Meta gave, for support", () => {
    expect(metaFaultMessage({ message: "Nope.", code: "4001", sessionId: "s-9" })).toBe("Nope. (code 4001, session s-9)");
    expect(metaFaultMessage({ message: "Nope.", code: null, sessionId: null })).toBe("Nope.");
    expect(metaFaultMessage({ message: null, code: "1", sessionId: null })).toBe("Meta reported an error. (code 1)");
  });

  it("calls a cancel a Meta failure only when it carries a reason", () => {
    expect(hasFault({ message: null, code: null, sessionId: "s" })).toBe(false);
    expect(hasFault({ message: "x", code: null, sessionId: null })).toBe(true);
    expect(hasFault({ message: null, code: "5", sessionId: null })).toBe(true);
  });
});
