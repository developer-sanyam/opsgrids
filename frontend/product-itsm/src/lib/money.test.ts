import { describe, expect, it } from "vitest";

import { currencyDecimals, minorToInput, parseMinor } from "./money";

describe("currencyDecimals", () => {
  it.each([
    ["INR", 2],
    ["USD", 2],
    ["usd", 2],
    ["JPY", 0],
    ["KRW", 0],
    ["KWD", 3],
    ["BHD", 3],
    ["ABC", 2],
  ])("%s has %i decimal places", (currency, places) => {
    expect(currencyDecimals(currency)).toBe(places);
  });
});

describe("minorToInput", () => {
  it("renders a whole amount with no decimals", () => {
    expect(minorToInput(21_900_000, "INR")).toBe("219000");
  });

  it("renders cents padded to two digits", () => {
    expect(minorToInput(2950, "INR")).toBe("29.50");
    expect(minorToInput(105, "USD")).toBe("1.05");
  });

  it("renders zero as a bare 0", () => {
    expect(minorToInput(0, "INR")).toBe("0");
    expect(minorToInput(0, "JPY")).toBe("0");
    expect(minorToInput(0, "KWD")).toBe("0");
  });

  it("renders a currency with no minor unit as the amount itself", () => {
    expect(minorToInput(150_000, "JPY")).toBe("150000");
    expect(minorToInput(1, "JPY")).toBe("1");
  });

  it("renders a three-decimal currency to the fil", () => {
    expect(minorToInput(1250, "KWD")).toBe("1.250");
    expect(minorToInput(5, "KWD")).toBe("0.005");
    expect(minorToInput(2000, "KWD")).toBe("2");
  });

  it("does not drop a zero that is not a decimal", () => {
    expect(minorToInput(1_000_000, "JPY")).toBe("1000000");
    expect(minorToInput(1_000_000, "INR")).toBe("10000");
  });
});

describe("parseMinor", () => {
  it("parses a whole-number amount", () => {
    expect(parseMinor("219000", "INR")).toEqual({ ok: true, minor: 21_900_000 });
  });

  it("parses two decimal places without float drift", () => {
    expect(parseMinor("29.35", "USD")).toEqual({ ok: true, minor: 2935 });
  });

  it("tolerates spaces and commas", () => {
    expect(parseMinor("2,19,000", "INR")).toEqual({ ok: true, minor: 21_900_000 });
  });

  it("treats an empty string as zero", () => {
    expect(parseMinor("", "INR")).toEqual({ ok: true, minor: 0 });
  });

  it("refuses more than two decimal places", () => {
    expect(parseMinor("29.359", "INR")).toEqual({
      ok: false,
      message: "Enter an amount in digits, like 24000 or 24000.50, with no currency sign or words and no more than two decimal places.",
    });
    expect(parseMinor("29.500", "USD").ok).toBe(false);
  });

  it("refuses a non-numeric amount", () => {
    const result = parseMinor("nope", "INR");
    expect(result.ok).toBe(false);
  });

  it("takes a yen amount as it is: 150000 yen is 150000 minor units, not 15000000", () => {
    expect(parseMinor("150000", "JPY")).toEqual({ ok: true, minor: 150_000 });
    expect(parseMinor("1,50,000", "jpy")).toEqual({ ok: true, minor: 150_000 });
  });

  it("refuses a decimal for a currency that has none", () => {
    expect(parseMinor("1500.5", "JPY")).toEqual({
      ok: false,
      message: "Enter a whole amount in digits, like 24000. JPY has no decimal places.",
    });
  });

  it("takes three decimals for a dinar, and no more", () => {
    expect(parseMinor("1.250", "KWD")).toEqual({ ok: true, minor: 1250 });
    expect(parseMinor("1.25", "KWD")).toEqual({ ok: true, minor: 1250 });
    expect(parseMinor("0.005", "KWD")).toEqual({ ok: true, minor: 5 });
    expect(parseMinor("1.2505", "KWD")).toEqual({
      ok: false,
      message: "Enter an amount in digits, like 24000 or 24000.500, with no currency sign or words and no more than three decimal places.",
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
