import { describe, expect, it } from "vitest";

import { currencyDecimals, minorToInput, parseMinor } from "./money";

describe("currencyDecimals", () => {
  it.each([
    ["INR", 2],
    ["USD", 2],
    ["usd", 2],
    ["JPY", 0],
    ["VND", 0],
    ["KWD", 3],
    ["OMR", 3],
    ["ABC", 2],
  ])("%s has %i decimal places", (currency, places) => {
    expect(currencyDecimals(currency)).toBe(places);
  });
});

describe("minorToInput", () => {
  it("drops the decimal when there are no paise", () => {
    expect(minorToInput(240000000, "INR")).toBe("2400000");
  });

  it("keeps two decimal places when there are paise", () => {
    expect(minorToInput(2950, "INR")).toBe("29.50");
    expect(minorToInput(105, "USD")).toBe("1.05");
  });

  it("keeps the sign on a negative amount", () => {
    expect(minorToInput(-150, "INR")).toBe("-1.50");
  });

  it("shows zero as 0 in every currency", () => {
    expect(minorToInput(0, "INR")).toBe("0");
    expect(minorToInput(0, "JPY")).toBe("0");
    expect(minorToInput(0, "KWD")).toBe("0");
  });

  it("renders a currency with no minor unit as the amount itself", () => {
    expect(minorToInput(1_500_000, "JPY")).toBe("1500000");
    expect(minorToInput(250_000, "JPY")).toBe("250000");
  });

  it("renders a three-decimal currency to the fil", () => {
    expect(minorToInput(12_345, "KWD")).toBe("12.345");
    expect(minorToInput(5, "KWD")).toBe("0.005");
    expect(minorToInput(2000, "KWD")).toBe("2");
  });
});

describe("parseMinor", () => {
  it("parses a whole-currency amount", () => {
    expect(parseMinor("24000", "INR")).toEqual({ ok: true, minor: 2400000 });
  });

  it("parses two decimal places without float drift", () => {
    // Math.round(29.35 * 100) is 2935 only by luck of rounding; the digit-based parse never
    // touches a float at all.
    expect(parseMinor("29.35", "USD")).toEqual({ ok: true, minor: 2935 });
  });

  it("tolerates spaces and commas from a pasted amount", () => {
    expect(parseMinor("24,000.50", "INR")).toEqual({ ok: true, minor: 2400050 });
  });

  it("treats an empty string as zero", () => {
    expect(parseMinor("", "INR")).toEqual({ ok: true, minor: 0 });
  });

  it("refuses more than two decimal places, saying so as it always has", () => {
    expect(parseMinor("24000.5055", "INR")).toEqual({
      ok: false,
      message: "Enter a whole-currency amount, like 24000 or 24000.50. No more than two decimal places.",
    });
  });

  it("refuses a non-numeric amount", () => {
    const result = parseMinor("twenty", "INR");
    expect(result.ok).toBe(false);
  });

  it("takes a yen amount as it is: 1500000 yen is 1500000 minor units, not 150000000", () => {
    expect(parseMinor("1500000", "JPY")).toEqual({ ok: true, minor: 1_500_000 });
    expect(parseMinor("15,00,000", "jpy")).toEqual({ ok: true, minor: 1_500_000 });
  });

  it("refuses a decimal for a currency that has none", () => {
    expect(parseMinor("1500.5", "JPY")).toEqual({
      ok: false,
      message: "Enter a whole-currency amount, like 24000. JPY has no decimal places.",
    });
  });

  it("takes three decimals for a dinar, and no more", () => {
    expect(parseMinor("12.345", "KWD")).toEqual({ ok: true, minor: 12_345 });
    expect(parseMinor("12.5", "KWD")).toEqual({ ok: true, minor: 12_500 });
    expect(parseMinor("1.2505", "KWD")).toEqual({
      ok: false,
      message: "Enter a whole-currency amount, like 24000 or 24000.500. No more than three decimal places.",
    });
  });

  it("refuses an amount too large to store exactly", () => {
    expect(parseMinor("90071992547409.92", "INR")).toEqual({ ok: false, message: "That amount is too large." });
  });

  it("reads the amount in the currency it is given, so the same digits differ by currency", () => {
    expect(parseMinor("100", "INR")).toEqual({ ok: true, minor: 10_000 });
    expect(parseMinor("100", "JPY")).toEqual({ ok: true, minor: 100 });
    expect(parseMinor("100", "KWD")).toEqual({ ok: true, minor: 100_000 });
  });
});
