import { describe, expect, it } from "vitest";

import { minorToMajor } from "./money";

describe("minorToMajor", () => {
  it.each([
    [2950, "INR", "29.50"],
    [8450000, "INR", "84500.00"],
    [5, "USD", "0.05"],
    [0, "INR", "0.00"],
    [2934, "usd", "29.34"],
  ])("writes %i %s as %s (two decimals by default)", (minor, currency, expected) => {
    expect(minorToMajor(minor, currency)).toBe(expected);
  });

  it.each([
    [2950, "JPY", "2950"],
    [0, "KRW", "0"],
    [120, "VND", "120"],
  ])("writes %i %s as %s (no decimals)", (minor, currency, expected) => {
    expect(minorToMajor(minor, currency)).toBe(expected);
  });

  it.each([
    [1250, "KWD", "1.250"],
    [5, "BHD", "0.005"],
    [-5, "OMR", "-0.005"],
  ])("writes %i %s as %s (three decimals)", (minor, currency, expected) => {
    expect(minorToMajor(minor, currency)).toBe(expected);
  });

  it("is exact where a float would not be", () => {
    expect(minorToMajor(2935, "INR")).toBe("29.35");
    expect(minorToMajor(9007199254740991, "INR")).toBe("90071992547409.91");
  });
});
