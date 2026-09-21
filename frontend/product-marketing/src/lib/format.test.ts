import { describe, expect, it } from "vitest";

import { formatDate, formatMoney, formatRoas, minorToInput, parseMoneyToMinor } from "./format";

describe("parseMoneyToMinor", () => {
  it("parses a whole amount", () => {
    expect(parseMoneyToMinor("24000")).toEqual({ ok: true, minor: 2400000 });
  });

  it("parses two decimal places without touching a float", () => {
    // Math.round(29.35 * 100) happens to land on 2935; the digit-based parse never risks it.
    expect(parseMoneyToMinor("29.35")).toEqual({ ok: true, minor: 2935 });
  });

  it("rounds neither up nor down — it refuses a third decimal place instead", () => {
    const result = parseMoneyToMinor("1250.505");
    expect(result.ok).toBe(false);
  });

  it("pads a single decimal place: 1250.5 -> 125050 paise", () => {
    expect(parseMoneyToMinor("1250.5")).toEqual({ ok: true, minor: 125050 });
  });

  it("tolerates spaces, commas and a currency sign from a pasted amount", () => {
    expect(parseMoneyToMinor("₹12,50,000.50")).toEqual({ ok: true, minor: 125000050 });
  });

  it("treats an empty field as zero — every money field on a campaign defaults to 0", () => {
    expect(parseMoneyToMinor("")).toEqual({ ok: true, minor: 0 });
  });

  it("treats whitespace-only input as zero", () => {
    expect(parseMoneyToMinor("   ")).toEqual({ ok: true, minor: 0 });
  });

  it("refuses a negative amount — every money field is ge=0 server-side", () => {
    const result = parseMoneyToMinor("-500");
    expect(result.ok).toBe(false);
  });

  it("refuses a non-numeric amount", () => {
    const result = parseMoneyToMinor("twenty");
    expect(result.ok).toBe(false);
  });

  it("refuses an amount too large to stay a safe integer", () => {
    const result = parseMoneyToMinor("99999999999999999999");
    expect(result.ok).toBe(false);
  });
});

describe("minorToInput", () => {
  it("keeps two decimal places", () => {
    expect(minorToInput(125050)).toBe("1250.50");
  });

  it("round-trips through parseMoneyToMinor", () => {
    const parsed = parseMoneyToMinor(minorToInput(125050));
    expect(parsed).toEqual({ ok: true, minor: 125050 });
  });

  it("pads a single paisa digit", () => {
    expect(minorToInput(100005)).toBe("1000.05");
  });
});

describe("formatMoney", () => {
  it("divides by 100 exactly once and groups with the Indian convention", () => {
    expect(formatMoney(125000000, "INR")).toBe("₹12,50,000");
  });
});

describe("formatRoas", () => {
  it("reads a zero ratio (no spend) as an em dash, never NaN or Infinity", () => {
    expect(formatRoas(0)).toBe("—");
  });

  it("formats a real ratio to one decimal place", () => {
    expect(formatRoas(4.16)).toBe("4.2×");
  });
});

describe("formatDate", () => {
  it("returns an em dash for a null (open-ended) date", () => {
    expect(formatDate(null)).toBe("—");
  });

  it("formats an ISO date in UTC so it never shifts a day off", () => {
    expect(formatDate("2026-03-15")).toBe("15 Mar 2026");
  });
});
