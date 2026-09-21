import { describe, expect, it } from "vitest";

import { minorToInput, parseOptionalMinor, parseRequiredMinor } from "./money";

describe("parseRequiredMinor", () => {
  it("parses whole rupees and paise on the string, never a float multiplication", () => {
    // 29.35 * 100 is 2934.9999999999995 in floating point — the regression this guards.
    expect(parseRequiredMinor("29.35", "a price")).toEqual({ ok: true, minor: 2935 });
    expect(parseRequiredMinor("199", "a price")).toEqual({ ok: true, minor: 19900 });
  });

  it("refuses empty, since a price is never optional", () => {
    expect(parseRequiredMinor("", "a price")).toMatchObject({ ok: false });
  });

  it("refuses more than two decimal places rather than rounding", () => {
    expect(parseRequiredMinor("29.999", "a price")).toMatchObject({ ok: false });
  });

  it("tolerates spaces, commas and a rupee sign", () => {
    expect(parseRequiredMinor("₹1,999.50", "a price")).toEqual({ ok: true, minor: 199950 });
  });
});

describe("parseOptionalMinor", () => {
  it("returns null for empty, never zero", () => {
    expect(parseOptionalMinor("")).toEqual({ ok: true, minor: null });
  });

  it("parses a value when one is given", () => {
    expect(parseOptionalMinor("49.50")).toEqual({ ok: true, minor: 4950 });
  });
});

describe("minorToInput", () => {
  it("omits paise when there are none, and prints them when there are", () => {
    expect(minorToInput(2900)).toBe("29");
    expect(minorToInput(2950)).toBe("29.50");
  });

  it("renders null as an empty string", () => {
    expect(minorToInput(null)).toBe("");
  });
});
