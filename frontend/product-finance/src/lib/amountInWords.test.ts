import { describe, expect, it } from "vitest";

import { amountInWords } from "./amountInWords";

describe("amountInWords", () => {
  it("says zero as zero rupees", () => {
    expect(amountInWords(0)).toBe("Indian Rupees Zero Only");
  });

  it("reads paise on their own without a rupee clause", () => {
    expect(amountInWords(50)).toBe("Fifty Paise Only");
    expect(amountInWords(1)).toBe("One Paisa Only");
    expect(amountInWords(99)).toBe("Ninety-Nine Paise Only");
  });

  it("joins rupees and paise with 'and'", () => {
    expect(amountInWords(1250)).toBe("Indian Rupees Twelve and Fifty Paise Only");
    expect(amountInWords(10_001)).toBe("Indian Rupees One Hundred and One Paisa Only");
  });

  it("uses Indian grouping for ₹1,41,600.00", () => {
    expect(amountInWords(14_160_000)).toBe(
      "Indian Rupees One Lakh Forty-One Thousand Six Hundred Only",
    );
  });

  it("reads crores, skipping empty groups", () => {
    expect(amountInWords(1_000_000_000)).toBe("Indian Rupees One Crore Only");
    expect(amountInWords(123_456_789_00)).toBe(
      "Indian Rupees Twelve Crore Thirty-Four Lakh Fifty-Six Thousand Seven Hundred Eighty-Nine Only",
    );
    expect(amountInWords(5_000_000_500)).toBe("Indian Rupees Five Crore Five Only");
  });

  it("spells out the crore count above ninety-nine crore", () => {
    expect(amountInWords(123 * 10_000_000 * 100)).toBe(
      "Indian Rupees One Hundred Twenty-Three Crore Only",
    );
  });

  it("handles the teens and round tens", () => {
    expect(amountInWords(1900)).toBe("Indian Rupees Nineteen Only");
    expect(amountInWords(2_000_000)).toBe("Indian Rupees Twenty Thousand Only");
    expect(amountInWords(10_000_000)).toBe("Indian Rupees One Lakh Only");
  });

  it("refuses a fractional paise count rather than rounding it", () => {
    expect(() => amountInWords(10.5)).toThrow(RangeError);
  });
});
