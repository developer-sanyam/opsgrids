import { describe, expect, it } from "vitest";

import { formatMoney, formatMoneyCompact, formatMoneyPrecise } from "./format";

describe("operations money formatting is exponent-aware", () => {
  it("keeps INR output unchanged", () => {
    expect(formatMoney(1245000000, "INR")).toBe("₹1,24,50,000");
    expect(formatMoneyPrecise(1245075, "INR")).toBe("₹12,450.75");
    expect(formatMoneyCompact(1250000000, "INR")).toBe("₹1.3 Cr");
    expect(formatMoneyCompact(420000, "INR")).toBe("₹4,200");
    expect(formatMoneyCompact(8_180_000, "INR")).toBe("₹81,800");
  });

  it("does not divide a zero-decimal currency by 100", () => {
    expect(formatMoney(125000, "JPY")).toBe("JP¥1,25,000");
    expect(formatMoneyPrecise(125000, "JPY")).toBe("JP¥1,25,000");
    expect(formatMoneyCompact(125000, "JPY")).toBe("JP¥125K");
  });

  it("divides a three-decimal currency by 1000", () => {
    expect(formatMoney(12_500_000, "KWD")).toBe("KWD\u00a012,500");
    expect(formatMoneyPrecise(12_345_678, "KWD")).toBe("KWD\u00a012,345.678");
    expect(formatMoneyCompact(12_500_000, "KWD")).toBe("KWD 12.5K");
  });
});
