import { describe, expect, it } from "vitest";

import {
  formatMicros,
  formatMinor,
  microsToInput,
  minorToInput,
  parseAmountToMinor,
  parseRateToMicros,
} from "./minorUnits";

describe("parseAmountToMinor — digits as text, the currency's own exponent, no float", () => {
  it.each([
    ["1250.50", "INR", 125050],
    ["1,250.5", "INR", 125050],
    ["19.99", "USD", 1999],
    ["0.29", "USD", 29],
    ["1200", "JPY", 1200],
    ["1.250", "KWD", 1250],
    ["0", "INR", 0],
  ])("%s %s → %i", (text, currency, minor) => {
    expect(parseAmountToMinor(text, currency)).toEqual({ ok: true, value: minor });
  });

  it("never rounds through binary floating point (19.99 * 100 is 1998.9999…)", () => {
    expect(parseAmountToMinor("4.35", "USD")).toEqual({ ok: true, value: 435 });
    expect(parseAmountToMinor("1.005", "KWD")).toEqual({ ok: true, value: 1005 });
  });

  it.each([
    ["", "INR", "Enter an amount."],
    ["12.345", "INR", "Use at most 2 decimal places."],
    ["12.5", "JPY", "This currency has no minor unit — enter a whole number."],
    ["-5", "INR", "Enter an amount as digits, like 1250 or 1250.50."],
    ["1e3", "INR", "Enter an amount as digits, like 1250 or 1250.50."],
    ["99999999999999999", "INR", "That is too large."],
  ])("refuses %j in %s", (text, currency, error) => {
    expect(parseAmountToMinor(text, currency)).toEqual({ ok: false, error });
  });
});

describe("a comma is a group separator only where it groups — never a silent decimal comma", () => {
  it.each([
    ["1,250", "INR", 125000],
    ["1,250,000.75", "USD", 125000075],
    ["12,50,000", "INR", 125000000],
    ["1,25,000.50", "INR", 12500050],
    ["1 250", "EUR", 125000],
  ])("accepts grouped %s %s → %i", (text, currency, minor) => {
    expect(parseAmountToMinor(text, currency)).toEqual({ ok: true, value: minor });
  });

  it.each([
    ["1,99", "EUR"],
    ["1,5", "INR"],
    ["12,5", "USD"],
    ["1,2345", "INR"],
    ["1,250.5,0", "INR"],
    ["1.25,0", "INR"],
    [",250", "INR"],
  ])("refuses %j in %s instead of scaling it", (text, currency) => {
    expect(parseAmountToMinor(text, currency)).toEqual({
      ok: false,
      error: "Use a dot for decimals, like 1250.50. A comma may only group thousands (1,250.50).",
    });
  });
});

describe("parseRateToMicros — BigInt micros of a major unit", () => {
  it("parses up to six decimal places exactly", () => {
    expect(parseRateToMicros("0.0025")).toEqual({ ok: true, value: 2500 });
    expect(parseRateToMicros("1.000001")).toEqual({ ok: true, value: 1_000_001 });
  });

  it.each(["0,0025", "1,000", "0 0025"])("refuses any separator in a rate: %j", (text) => {
    expect(parseRateToMicros(text)).toEqual({
      ok: false,
      error: "Use a dot for decimals, like 0.0025, with no commas or spaces.",
    });
  });

  it("refuses a seventh decimal place", () => {
    expect(parseRateToMicros("0.0000001")).toEqual({ ok: false, error: "Use at most 6 decimal places." });
  });
});

describe("formatting round-trips", () => {
  it.each([
    [125050, "INR", "1250.50", "INR 1,250.50"],
    [1200, "JPY", "1200", "JPY 1,200"],
    [1250, "KWD", "1.250", "KWD 1.250"],
    [5, "USD", "0.05", "USD 0.05"],
  ])("%i %s", (minor, currency, input, display) => {
    expect(minorToInput(minor, currency)).toBe(input);
    expect(parseAmountToMinor(input, currency)).toEqual({ ok: true, value: minor });
    expect(formatMinor(minor, currency)).toBe(display);
  });

  it("trims a rate's trailing zeros", () => {
    expect(microsToInput(2500)).toBe("0.0025");
    expect(microsToInput(3_000_000)).toBe("3");
    expect(formatMicros(2500, "INR")).toBe("INR 0.0025");
  });
});
