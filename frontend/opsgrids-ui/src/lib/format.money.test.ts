import { describe, expect, it } from "vitest";

import { currencyExponent, formatMoney, minorToInput, parseMinor, toMajor } from "./format";

describe("formatMoney", () => {
  it("formats INR and USD exactly as the CRM did: grouped en-IN, no fraction", () => {
    expect(formatMoney(1_250_000_00, "INR")).toBe("₹12,50,000");
    expect(formatMoney(1_250_000_00, "USD")).toBe("$12,50,000");
    expect(formatMoney(29_50, "INR")).toBe("₹30");
  });

  it("compacts INR in crore and lakh, and other currencies in K, M and B, as the CRM did", () => {
    expect(formatMoney(3_20_00_000_00, "INR", { compact: true })).toBe("₹3.2 Cr");
    expect(formatMoney(95_00_000_00, "INR", { compact: true })).toBe("₹95 L");
    expect(formatMoney(4_500_00, "INR", { compact: true })).toBe("₹4,500");
    expect(formatMoney(1_200_000_00, "USD", { compact: true })).toBe("$1.2M");
    expect(formatMoney(8_000_00, "USD", { compact: true })).toBe("$8K");
    expect(formatMoney(2_000_000_000_00, "USD", { compact: true })).toBe("$2B");
  });

  it("reads the currency's own exponent: JPY has no decimals, KWD has three", () => {
    expect(formatMoney(1234, "JPY")).toBe("JP¥1,234");
    expect(formatMoney(1234, "JPY", { precise: true })).toBe("JP¥1,234");
    expect(formatMoney(1_234_500, "KWD", { precise: true })).toBe("KWD\u00a01,234.500");
    expect(formatMoney(2935, "INR", { precise: true })).toBe("₹29.35");
  });

  it("takes a locale", () => {
    expect(formatMoney(1_250_000_00, "USD", { locale: "en-US" })).toBe("$1,250,000");
  });
});

describe("currencyExponent and toMajor", () => {
  it("answers 2, 0 and 3 for INR, JPY and KWD, and 2 for a code Intl cannot read", () => {
    expect(currencyExponent("INR")).toBe(2);
    expect(currencyExponent("JPY")).toBe(0);
    expect(currencyExponent("KWD")).toBe(3);
    expect(currencyExponent("ABCD")).toBe(2);
  });

  it("divides by the currency's exponent", () => {
    expect(toMajor(12_345, "INR")).toBe(123.45);
    expect(toMajor(12_345, "JPY")).toBe(12_345);
    expect(toMajor(12_345, "KWD")).toBe(12.345);
  });
});

describe("parseMinor", () => {
  it("parses whole amounts, two decimals and Indian grouping without float drift", () => {
    expect(parseMinor("24000", "INR")).toEqual({ ok: true, minor: 2_400_000 });
    expect(parseMinor("29.35", "INR")).toEqual({ ok: true, minor: 2935 });
    expect(parseMinor("84,00,000", "INR")).toEqual({ ok: true, minor: 840_000_000 });
    expect(parseMinor(" 500 ", "INR")).toEqual({ ok: true, minor: 50_000 });
    expect(parseMinor("1,20,000.5", "INR")).toEqual({ ok: true, minor: 12_000_050 });
  });

  it("treats a blank as zero, never null", () => {
    expect(parseMinor("", "INR")).toEqual({ ok: true, minor: 0 });
  });

  it("follows the currency's exponent", () => {
    expect(parseMinor("1234", "JPY")).toEqual({ ok: true, minor: 1234 });
    expect(parseMinor("1.234", "KWD")).toEqual({ ok: true, minor: 1234 });
    expect(parseMinor("12.5", "JPY").ok).toBe(false);
    expect(parseMinor("1.2345", "KWD").ok).toBe(false);
  });

  it.each(["1.200,50", "₹500", "$500", "12 lakh", "29.351", "-500", "not a number", "1e5"])(
    "refuses %s with a message that states the accepted form",
    (input) => {
      const result = parseMinor(input, "INR");
      expect(result.ok).toBe(false);
      if (!result.ok) expect(result.message).toContain("like 24000 or 24000.50");
    },
  );

  it("says JPY takes whole amounts only", () => {
    const result = parseMinor("12.5", "JPY");
    expect(result).toEqual({ ok: false, message: "Enter a whole amount in digits, like 24000. JPY has no decimal places." });
  });

  it("refuses an amount too large to hold exactly", () => {
    expect(parseMinor("90071992547409.92", "INR")).toEqual({ ok: false, message: "That amount is too large." });
  });
});

describe("minorToInput", () => {
  it("drops an all-zero fraction and pads the rest to the exponent", () => {
    expect(minorToInput(2_400_000, "INR")).toBe("24000");
    expect(minorToInput(2935, "INR")).toBe("29.35");
    expect(minorToInput(2950, "INR")).toBe("29.50");
    expect(minorToInput(1234, "JPY")).toBe("1234");
    expect(minorToInput(1500, "KWD")).toBe("1.500");
  });

  it.each([
    [8_400_000_000, "INR"],
    [2935, "INR"],
    [1234, "JPY"],
    [1501, "KWD"],
  ] as const)("round-trips %i %s through parseMinor", (minor, currency) => {
    expect(parseMinor(minorToInput(minor, currency), currency)).toEqual({ ok: true, minor });
  });
});
