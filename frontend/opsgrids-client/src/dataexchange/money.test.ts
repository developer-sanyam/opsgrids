import { describe, expect, it } from "vitest";

import { compareDecimals, currencyExponent, majorToMinor, minorToMajor, plainNumberString } from "./money";
import { RowProblem } from "./types";

describe("currencyExponent", () => {
  it("mirrors the backend table and defaults to two places", () => {
    for (const code of ["JPY", "KRW", "VND", "CLP", "ISK"]) expect(currencyExponent(code)).toBe(0);
    for (const code of ["BHD", "KWD", "OMR", "TND", "JOD"]) expect(currencyExponent(code)).toBe(3);
    for (const code of ["INR", "USD", "EUR", "XYZ"]) expect(currencyExponent(code)).toBe(2);
  });

  it("ignores case and surrounding spaces", () => {
    expect(currencyExponent(" jpy ")).toBe(0);
    expect(currencyExponent("kwd")).toBe(3);
  });
});

describe("majorToMinor", () => {
  it("converts by the currency's own exponent", () => {
    expect(majorToMinor("1499.50", "INR")).toBe(149950);
    expect(majorToMinor("1499", "JPY")).toBe(1499);
    expect(majorToMinor("1.234", "KWD")).toBe(1234);
    expect(majorToMinor("0.5", "KWD")).toBe(500);
  });

  it("is exact where float arithmetic is not", () => {
    // 19.99 * 100 is 1998.9999999999998 in IEEE 754, and 0.29 * 100 is 28.999999999999996.
    expect(majorToMinor("19.99", "INR")).toBe(1999);
    expect(majorToMinor("0.29", "INR")).toBe(29);
    expect(majorToMinor("0.1", "INR")).toBe(10);
    expect(majorToMinor(0.1, "INR")).toBe(10);
    expect(majorToMinor(1.15, "USD")).toBe(115);
    expect(majorToMinor(4.35, "USD")).toBe(435);
  });

  it("removes the float noise a spreadsheet formula leaves behind", () => {
    expect(majorToMinor(59.970000000000006, "INR")).toBe(5997);
    expect(majorToMinor(0.1 + 0.2, "INR")).toBe(30);
  });

  it("accepts a leading dot, leading zeros and surrounding spaces", () => {
    expect(majorToMinor(".5", "INR")).toBe(50);
    expect(majorToMinor("007.10", "INR")).toBe(710);
    expect(majorToMinor("  12.30 ", "INR")).toBe(1230);
  });

  it("accepts trailing zeros that add no precision", () => {
    expect(majorToMinor("100.500", "INR")).toBe(10050);
    expect(majorToMinor("100.0", "JPY")).toBe(100);
  });

  it("refuses more decimals than the currency has, instead of rounding", () => {
    expect(() => majorToMinor("1.005", "INR")).toThrow(RowProblem);
    expect(() => majorToMinor("1.005", "INR")).toThrow(/at most 2 decimal places for INR/);
    expect(() => majorToMinor("1.5", "JPY")).toThrow(/whole number for JPY/);
    expect(() => majorToMinor("1.2345", "KWD")).toThrow(/at most 3 decimal places/);
    expect(() => majorToMinor(1.005, "INR")).toThrow(RowProblem);
  });

  it("refuses negatives", () => {
    expect(() => majorToMinor("-5", "INR")).toThrow(/cannot be negative/);
    expect(() => majorToMinor(-0.5, "INR")).toThrow(/cannot be negative/);
  });

  it("refuses exponent notation", () => {
    expect(() => majorToMinor("1e5", "INR")).toThrow(/exponent/);
    expect(() => majorToMinor("1.5E3", "INR")).toThrow(/exponent/);
  });

  it("refuses thousands separators and a decimal comma", () => {
    expect(() => majorToMinor("1,000.50", "INR")).toThrow(/commas/);
    expect(() => majorToMinor("1 000", "INR")).toThrow(/commas or spaces/);
    expect(() => majorToMinor("1,5", "EUR")).toThrow(/commas/);
    expect(() => majorToMinor("1_000", "INR")).toThrow(/commas or spaces/);
  });

  it("refuses anything that is not a plain number", () => {
    for (const text of ["", "abc", "₹100", "$5", "12.", "+5", "5-", "1.2.3", "NaN", "Infinity"]) {
      expect(() => majorToMinor(text, "INR"), text).toThrow(RowProblem);
    }
    expect(() => majorToMinor(Number.NaN, "INR")).toThrow(RowProblem);
    expect(() => majorToMinor(Number.POSITIVE_INFINITY, "INR")).toThrow(RowProblem);
  });

  it("refuses an amount beyond what a safe integer can hold", () => {
    expect(() => majorToMinor("999999999999999999", "INR")).toThrow(/too large/);
    expect(majorToMinor("90071992547409.91", "INR")).toBe(9007199254740991);
  });
});

describe("minorToMajor", () => {
  it("writes an exact decimal with the currency's places", () => {
    expect(minorToMajor(149950, "INR")).toBe("1499.50");
    expect(minorToMajor(5, "INR")).toBe("0.05");
    expect(minorToMajor(0, "INR")).toBe("0.00");
    expect(minorToMajor(1499, "JPY")).toBe("1499");
    expect(minorToMajor(1234, "KWD")).toBe("1.234");
    expect(minorToMajor(7, "KWD")).toBe("0.007");
    expect(minorToMajor(-250, "USD")).toBe("-2.50");
  });

  it("round-trips through majorToMinor", () => {
    for (const [minor, currency] of [[1, "INR"], [99999999, "INR"], [12, "JPY"], [1001, "KWD"]] as const) {
      expect(majorToMinor(minorToMajor(minor, currency), currency)).toBe(minor);
    }
  });

  it("refuses a fraction of a minor unit", () => {
    expect(() => minorToMajor(1.5, "INR")).toThrow(RangeError);
    expect(() => minorToMajor(Number.NaN, "INR")).toThrow(RangeError);
  });
});

describe("plainNumberString", () => {
  it("never uses exponent notation", () => {
    expect(plainNumberString(1e21)).toBe("1000000000000000000000");
    expect(plainNumberString(1e-7)).toBe("0.0000001");
    expect(plainNumberString(123456789012345680000)).toBe("123456789012346000000");
  });

  it("keeps a safe integer exact and rounds float noise to 15 digits", () => {
    expect(plainNumberString(9007199254740991)).toBe("9007199254740991");
    expect(plainNumberString(-12)).toBe("-12");
    expect(plainNumberString(0.30000000000000004)).toBe("0.3");
    expect(plainNumberString(-0.5)).toBe("-0.5");
  });

  it("gives null for a number that is not finite", () => {
    expect(plainNumberString(Number.NaN)).toBeNull();
    expect(plainNumberString(Number.NEGATIVE_INFINITY)).toBeNull();
  });
});

describe("compareDecimals", () => {
  it("compares exactly across different scales and signs", () => {
    expect(compareDecimals("1.10", "1.1")).toBe(0);
    expect(compareDecimals("0.001", "0.0009")).toBe(1);
    expect(compareDecimals("-1", "0.5")).toBe(-1);
    expect(compareDecimals("100", "99.99")).toBe(1);
  });
});
