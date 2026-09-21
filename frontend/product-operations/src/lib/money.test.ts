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
    const result = parseMinor("29.355");
    expect(result.ok).toBe(false);
  });

  it("refuses a non-numeric string", () => {
    const result = parseMinor("abc");
    expect(result.ok).toBe(false);
  });

  it("refuses a negative amount", () => {
    const result = parseMinor("-100");
    expect(result.ok).toBe(false);
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
