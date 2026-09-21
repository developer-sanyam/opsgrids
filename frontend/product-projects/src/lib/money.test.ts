import { describe, expect, it } from "vitest";

import { minorToInput, parseMinor } from "./money";

describe("minorToInput", () => {
  it("drops the decimal when there are no paise", () => {
    expect(minorToInput(240000000)).toBe("2400000");
  });

  it("keeps two decimal places when there are paise", () => {
    expect(minorToInput(2950)).toBe("29.50");
  });

  it("keeps the sign on a negative amount", () => {
    expect(minorToInput(-150)).toBe("-1.50");
  });
});

describe("parseMinor", () => {
  it("parses a whole-currency amount", () => {
    expect(parseMinor("24000")).toEqual({ ok: true, minor: 2400000 });
  });

  it("parses two decimal places without float drift", () => {
    // Math.round(29.35 * 100) is 2935 only by luck of rounding; the digit-based parse never
    // touches a float at all.
    expect(parseMinor("29.35")).toEqual({ ok: true, minor: 2935 });
  });

  it("tolerates spaces and commas from a pasted amount", () => {
    expect(parseMinor("24,000.50")).toEqual({ ok: true, minor: 2400050 });
  });

  it("treats an empty string as zero", () => {
    expect(parseMinor("")).toEqual({ ok: true, minor: 0 });
  });

  it("refuses more than two decimal places", () => {
    const result = parseMinor("24000.5055");
    expect(result.ok).toBe(false);
  });

  it("refuses a non-numeric amount", () => {
    const result = parseMinor("twenty");
    expect(result.ok).toBe(false);
  });
});
