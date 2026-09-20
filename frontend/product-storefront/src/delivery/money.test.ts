import { describe, expect, it } from "vitest";

import { formatMinorExact, minorToInput, parseMinor } from "./money";

/**
 * Money is integer minor units with an ISO currency, everywhere except the input box (ADR-0000 §8).
 * These tests exist to keep a float out of the middle: `29.35 * 100` is `2934.9999999999995`, and
 * a `Math.round` hiding that works until the amount is large enough that it does not.
 */

describe("parseMinor", () => {
  it("reads whole rupees and paise without a float in the middle", () => {
    expect(parseMinor("29")).toEqual({ ok: true, minor: 2900 });
    expect(parseMinor("29.50")).toEqual({ ok: true, minor: 2950 });
    expect(parseMinor("29.5")).toEqual({ ok: true, minor: 2950 });
    expect(parseMinor("29.35")).toEqual({ ok: true, minor: 2935 });
    expect(parseMinor("0")).toEqual({ ok: true, minor: 0 });
  });

  it("tolerates what people paste", () => {
    expect(parseMinor(" ₹1,499.00 ")).toEqual({ ok: true, minor: 149900 });
  });

  it("treats empty as 'nothing set', which is not a fee of zero", () => {
    expect(parseMinor("")).toEqual({ ok: true, minor: null });
    expect(parseMinor("   ")).toEqual({ ok: true, minor: null });
  });

  it("refuses more than two decimal places instead of rounding them away", () => {
    const result = parseMinor("29.999");
    expect(result.ok).toBe(false);
  });

  it("refuses anything that is not an amount", () => {
    expect(parseMinor("free").ok).toBe(false);
    expect(parseMinor("-5").ok).toBe(false);
  });
});

describe("minorToInput", () => {
  it("round-trips through the form without gaining decimals", () => {
    expect(minorToInput(2900)).toBe("29");
    expect(minorToInput(2950)).toBe("29.50");
    expect(minorToInput(2905)).toBe("29.05");
    expect(minorToInput(null)).toBe("");
  });
});

describe("formatMinorExact", () => {
  it("shows paise only when there are paise", () => {
    expect(formatMinorExact(2900, "INR")).toContain("29");
    expect(formatMinorExact(2900, "INR")).not.toContain(".");
    expect(formatMinorExact(2950, "INR")).toContain("29.50");
  });

  it("still prints the number when the currency code is unrecognised", () => {
    // The amount is the fact; the symbol is decoration. `Intl` throws rather than falling back.
    expect(formatMinorExact(2950, "XYZ_NOT_A_CURRENCY")).toContain("29.50");
  });
});
