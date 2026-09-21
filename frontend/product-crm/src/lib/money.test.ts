import { describe, expect, it } from "vitest";

import { minorToInput, parseMinor } from "./money";

describe("parseMinor", () => {
  it("parses a whole-number amount into minor units", () => {
    expect(parseMinor("24000")).toEqual({ ok: true, minor: 2_400_000 });
  });

  it("parses two decimal places without float drift", () => {
    expect(parseMinor("29.35")).toEqual({ ok: true, minor: 2935 });
  });

  it("tolerates spaces and commas — people paste amounts", () => {
    // "84,00,000" is the Indian grouping of 84,00,000 = 8,400,000 rupees.
    expect(parseMinor("84,00,000")).toEqual({ ok: true, minor: 840_000_000 });
    expect(parseMinor(" 500 ")).toEqual({ ok: true, minor: 50_000 });
  });

  it("treats an empty string as zero, never null", () => {
    expect(parseMinor("")).toEqual({ ok: true, minor: 0 });
  });

  it("refuses more than two decimal places", () => {
    const result = parseMinor("29.351");
    expect(result.ok).toBe(false);
  });

  it("refuses a non-numeric string", () => {
    const result = parseMinor("not a number");
    expect(result.ok).toBe(false);
  });

  it("refuses a negative amount", () => {
    const result = parseMinor("-500");
    expect(result.ok).toBe(false);
  });
});

describe("minorToInput", () => {
  it("drops a trailing .00", () => {
    expect(minorToInput(2_400_000)).toBe("24000");
  });

  it("keeps two decimal places when there are paise", () => {
    expect(minorToInput(2935)).toBe("29.35");
  });

  it("round-trips through parseMinor", () => {
    const minor = 8_400_000_000;
    const input = minorToInput(minor);
    expect(parseMinor(input)).toEqual({ ok: true, minor });
  });
});
