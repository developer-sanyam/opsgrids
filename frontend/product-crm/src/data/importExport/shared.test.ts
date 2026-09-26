import { describe, expect, it } from "vitest";

import { minorToMajor } from "./shared";

describe("minorToMajor", () => {
  it("writes two decimals for the default currency, exactly", () => {
    expect(minorToMajor(840000000000, "INR")).toBe("8400000000.00");
    expect(minorToMajor(2935, "USD")).toBe("29.35");
    expect(minorToMajor(5, "INR")).toBe("0.05");
    expect(minorToMajor(0, "INR")).toBe("0.00");
  });

  it("writes no decimals for a zero-decimal currency", () => {
    expect(minorToMajor(500, "JPY")).toBe("500");
    expect(minorToMajor(0, "KRW")).toBe("0");
  });

  it("writes three decimals for a three-decimal currency", () => {
    expect(minorToMajor(1500, "KWD")).toBe("1.500");
    expect(minorToMajor(7, "BHD")).toBe("0.007");
  });

  it("ignores the case of the currency code", () => {
    expect(minorToMajor(1500, "kwd")).toBe("1.500");
  });

  it("keeps the sign", () => {
    expect(minorToMajor(-2935, "INR")).toBe("-29.35");
    expect(minorToMajor(-5, "INR")).toBe("-0.05");
  });

  it("stays exact past the point where a float would round", () => {
    // 9007199254740991 is the largest safe integer; a float division would lose the last digits.
    expect(minorToMajor(9007199254740991, "INR")).toBe("90071992547409.91");
  });
});
