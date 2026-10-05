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

describe("parseRateToMicros — BigInt micros of a major unit", () => {
  it("parses up to six decimal places exactly", () => {
    expect(parseRateToMicros("0.0025")).toEqual({ ok: true, value: 2500 });
    expect(parseRateToMicros("1.000001")).toEqual({ ok: true, value: 1_000_001 });
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
