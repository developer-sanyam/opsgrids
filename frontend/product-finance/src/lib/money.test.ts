import { describe, expect, it } from "vitest";

import { formatMoneySigned, minorToInput, parseMinor, parseQuantity, sumMinor } from "./money";

describe("parseMinor", () => {
  it("reads a whole-rupee amount into paise", () => {
    expect(parseMinor("24000")).toEqual({ ok: true, minor: 2_400_000 });
  });

  it("reads a two-decimal amount without float drift", () => {
    // `29.35 * 100` in plain float arithmetic is `2934.9999999999995` — this must never drift.
    expect(parseMinor("29.35")).toEqual({ ok: true, minor: 2935 });
  });

  it("tolerates spaces, commas and the rupee sign", () => {
    expect(parseMinor("₹12,340.50")).toEqual({ ok: true, minor: 1_234_050 });
  });

  it("reads a negative amount, for a credit note line", () => {
    expect(parseMinor("-500.25")).toEqual({ ok: true, minor: -50_025 });
  });

  it("refuses more than two decimal places", () => {
    const result = parseMinor("10.999");
    expect(result.ok).toBe(false);
  });

  it("refuses an empty string", () => {
    const result = parseMinor("");
    expect(result.ok).toBe(false);
  });

  it("refuses a non-numeric string", () => {
    const result = parseMinor("abc");
    expect(result.ok).toBe(false);
  });
});

describe("minorToInput", () => {
  it("round-trips through parseMinor", () => {
    expect(minorToInput(2_400_000)).toBe("24000.00");
    expect(minorToInput(2935)).toBe("29.35");
    expect(minorToInput(-50_025)).toBe("-500.25");
  });
});

describe("parseQuantity", () => {
  it("reads a positive fractional quantity", () => {
    expect(parseQuantity("18.5")).toEqual({ ok: true, value: 18.5 });
  });

  it("reads a negative quantity, for a credit note line", () => {
    expect(parseQuantity("-30")).toEqual({ ok: true, value: -30 });
  });

  it("refuses zero", () => {
    expect(parseQuantity("0").ok).toBe(false);
  });

  it("refuses an empty string", () => {
    expect(parseQuantity("").ok).toBe(false);
  });
});

describe("sumMinor", () => {
  it("sums a list of minor amounts", () => {
    expect(sumMinor([100, 200, -50])).toBe(250);
  });

  it("sums an empty list to zero", () => {
    expect(sumMinor([])).toBe(0);
  });
});

describe("formatMoneySigned", () => {
  it("uses a true minus sign, not a hyphen", () => {
    expect(formatMoneySigned(-4500 * 100)).toContain("−");
    expect(formatMoneySigned(-4500 * 100)).not.toContain("-");
  });

  it("prefixes a positive amount with a plus", () => {
    expect(formatMoneySigned(4500 * 100).startsWith("+")).toBe(true);
  });
});
