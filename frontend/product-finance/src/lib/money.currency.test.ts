import { describe, expect, it } from "vitest";

import { formatAmount, formatMoney, formatMoneyCompact, formatMoneySigned, formatMoneyWhole } from "./money";

describe("finance money formatting is exponent-aware", () => {
  it("keeps INR output unchanged", () => {
    expect(formatMoney(123456700)).toBe("₹12,34,567.00");
    expect(formatMoney(-100)).toBe("₹-1.00");
    expect(formatAmount(123456750)).toBe("12,34,567.50");
    expect(formatMoneyWhole(123456750)).toBe("₹12,34,568");
    expect(formatMoneyCompact(123456700)).toBe("₹12.35L");
    expect(formatMoneyCompact(9676000)).toBe("₹96,760");
    expect(formatMoneyCompact(5840000000)).toBe("₹5.84Cr");
    expect(formatMoneyCompact(-123456700)).toBe("-₹12.35L");
    expect(formatMoneySigned(-450000)).toBe("−₹4,500.00");
  });

  it("does not divide a zero-decimal currency by 100", () => {
    expect(formatMoney(125000, "JPY")).toBe("JP¥1,25,000");
    expect(formatAmount(125000, "JPY")).toBe("1,25,000");
    expect(formatMoneyWhole(125000, "JPY")).toBe("JP¥1,25,000");
    expect(formatMoneyCompact(125000, "JPY")).toBe("JP¥1.25L");
  });

  it("divides a three-decimal currency by 1000", () => {
    expect(formatMoney(12_345_678, "KWD")).toBe("KWD 12,345.678");
    expect(formatAmount(12_345_678, "KWD")).toBe("12,345.678");
    expect(formatMoneyWhole(12_345_678, "KWD")).toBe("KWD 12,346");
    expect(formatMoneySigned(-12_345_678, "KWD")).toBe("−KWD 12,345.678");
  });
});
