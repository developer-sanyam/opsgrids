import { describe, expect, it } from "vitest";

import { minorToInput, parseInt0, parseMinor } from "./money";

describe("minorToInput", () => {
  it("renders a whole amount with no decimals", () => {
    expect(minorToInput(240000)).toBe("2400");
  });

  it("renders cents when present", () => {
    expect(minorToInput(2950)).toBe("29.50");
  });

  it("renders a negative amount with the sign in front", () => {
    expect(minorToInput(-1500)).toBe("-15");
  });

  it("renders zero as 0", () => {
    expect(minorToInput(0)).toBe("0");
  });
});

describe("parseMinor", () => {
  describe("optional amount (default) — the common case, blank means 0", () => {
    it("parses a whole-number amount", () => {
      expect(parseMinor("24000")).toEqual({ ok: true, minor: 2400000 });
    });

    it("parses two decimal places exactly", () => {
      expect(parseMinor("29.35")).toEqual({ ok: true, minor: 2935 });
    });

    it("tolerates spaces and commas", () => {
      expect(parseMinor("2,40,000")).toEqual({ ok: true, minor: 24000000 });
    });

    it("treats an empty string as zero", () => {
      expect(parseMinor("")).toEqual({ ok: true, minor: 0 });
    });

    it("refuses more than two decimal places", () => {
      expect(parseMinor("29.355").ok).toBe(false);
    });

    it("refuses a non-numeric string", () => {
      expect(parseMinor("abc").ok).toBe(false);
    });

    it("refuses a negative amount", () => {
      expect(parseMinor("-100").ok).toBe(false);
    });
  });

  describe("required amount — e.g. a purchase order line's unit price", () => {
    const unitPrice = { required: true, emptyMessage: "Enter a unit price." };

    it("reads whole rupees and paise without a float in the middle", () => {
      expect(parseMinor("2250", unitPrice)).toEqual({ ok: true, minor: 225000 });
      expect(parseMinor("2250.50", unitPrice)).toEqual({ ok: true, minor: 225050 });
      expect(parseMinor("2250.5", unitPrice)).toEqual({ ok: true, minor: 225050 });
    });

    it("keeps a value with paise exact", () => {
      expect(parseMinor("29.35", unitPrice)).toEqual({ ok: true, minor: 2935 });
      expect(parseMinor("0.05", unitPrice)).toEqual({ ok: true, minor: 5 });
    });

    it("parses a value that would drift through a naive float multiply", () => {
      // `2250.99 * 100` is `225098.99999999997` in IEEE 754 — a `Math.round` papering over that
      // works until the amount is large enough that it doesn't. The digit-based parse never
      // constructs that float at all, so it lands on the exact integer every time.
      expect(2250.99 * 100).not.toBe(225099);
      expect(parseMinor("2250.99", unitPrice)).toEqual({ ok: true, minor: 225099 });
    });

    it("tolerates what people paste", () => {
      expect(parseMinor(" ₹1,499.00 ", unitPrice)).toEqual({ ok: true, minor: 149900 });
    });

    it("refuses an empty price — a line's unit price is required, never optional", () => {
      expect(parseMinor("", unitPrice).ok).toBe(false);
      expect(parseMinor("   ", unitPrice).ok).toBe(false);
      expect(parseMinor("", unitPrice)).toEqual({ ok: false, message: "Enter a unit price." });
    });

    it("refuses more than two decimal places instead of rounding them away", () => {
      expect(parseMinor("29.999", unitPrice).ok).toBe(false);
    });

    it("refuses anything that is not an amount", () => {
      expect(parseMinor("free", unitPrice).ok).toBe(false);
      expect(parseMinor("-5", unitPrice).ok).toBe(false);
    });
  });
});

describe("parseInt0", () => {
  it("parses a whole number", () => {
    expect(parseInt0("40")).toEqual({ ok: true, value: 40 });
  });

  it("treats an empty string as zero", () => {
    expect(parseInt0("")).toEqual({ ok: true, value: 0 });
  });

  it("refuses a fraction", () => {
    expect(parseInt0("4.5").ok).toBe(false);
  });

  it("refuses a negative number", () => {
    expect(parseInt0("-4").ok).toBe(false);
  });
});
