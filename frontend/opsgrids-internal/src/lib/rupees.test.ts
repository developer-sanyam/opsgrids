import { describe, expect, it } from "vitest";

import { MAX_AMOUNT_MINOR, parseRupeesToPaise } from "./rupees";

describe("parseRupeesToPaise", () => {
  it.each([
    ["2499.50", 249_950],
    ["2499.5", 249_950],
    ["2499", 249_900],
    ["0.01", 1],
    ["0.99", 99],
    ["1", 100],
    ["  45000  ", 4_500_000],
    ["007.50", 750],
    [String(MAX_AMOUNT_MINOR / 100), MAX_AMOUNT_MINOR],
  ])("parses %s to exactly %i paise", (input, minor) => {
    const result = parseRupeesToPaise(input);
    expect(result.ok).toBe(true);
    if (result.ok) expect(result.minor).toBe(minor);
  });

  it.each([
    [""],
    ["   "],
    ["0"],
    ["0.00"],
    ["-5"],
    ["-45.50"],
    ["abc"],
    ["12.345"],
    ["2499.500"],
    ["1e5"],
    ["12,499"],
    ["+45"],
    [String(MAX_AMOUNT_MINOR / 100 + 0.01)],
  ])("rejects %s", (input) => {
    const result = parseRupeesToPaise(input);
    expect(result.ok).toBe(false);
  });

  it("names the specific problem for more than two decimal places", () => {
    const result = parseRupeesToPaise("12.345");
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.error).toBe("Enter at most 2 decimal places.");
  });

  it("names the specific problem for a negative amount", () => {
    const result = parseRupeesToPaise("-45.50");
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.error).toBe("Enter a positive amount.");
  });

  it("names the specific problem for zero", () => {
    const result = parseRupeesToPaise("0");
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.error).toBe("Enter an amount greater than zero.");
  });

  it("names the specific problem for an absurd amount", () => {
    const result = parseRupeesToPaise("999999999999");
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.error).toBe("That amount looks too large — check it and try again.");
  });

  it("never uses float arithmetic — a value Number() mangles still parses exactly", () => {
    // 0.1 + 0.2 !== 0.3 in IEEE-754; the classic float trap this function exists to avoid.
    const result = parseRupeesToPaise("0.30");
    expect(result).toEqual({ ok: true, minor: 30 });
  });
});
