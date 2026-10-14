import { describe, expect, it } from "vitest";

import { formatMoney, formatMoneyCompact } from "./format";

describe("marketing money formatting is exponent-aware", () => {
  it("keeps INR output unchanged", () => {
    expect(formatMoney(125000000, "INR")).toBe("₹12,50,000");
    expect(formatMoneyCompact(1420000000, "INR")).toBe("₹1.4Cr");
  });

  it("does not divide a zero-decimal currency by 100", () => {
    expect(formatMoney(125000, "JPY")).toBe("JP¥1,25,000");
    expect(formatMoneyCompact(125000, "JPY")).toBe("JP¥1.3L");
  });

  it("divides a three-decimal currency by 1000", () => {
    expect(formatMoney(12_500_000, "KWD")).toBe("KWD\u00a012,500");
    expect(formatMoneyCompact(12_500_000, "KWD")).toBe("KWD\u00a012.5K");
  });
});
