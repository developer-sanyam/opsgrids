import { describe, expect, it } from "vitest";

import { minorToInput, parseMinor } from "./money";

describe("minorToInput", () => {
  it("drops the cents when the amount is whole", () => {
    expect(minorToInput(2_400_000_000)).toBe("24000000");
  });

  it("keeps two decimal places when there are cents", () => {
    expect(minorToInput(2950)).toBe("29.50");
  });

  it("carries a negative sign through", () => {
    expect(minorToInput(-500)).toBe("-5");
  });

  it("renders zero as a bare zero", () => {
    expect(minorToInput(0)).toBe("0");
  });
});

describe("parseMinor", () => {
  it("parses a whole number of major units", () => {
    expect(parseMinor("24000")).toEqual({ ok: true, minor: 2_400_000 });
  });

  it("parses two decimal places without float drift — 29.35 is exactly 2935, not 2934.9999…", () => {
    expect(parseMinor("29.35")).toEqual({ ok: true, minor: 2935 });
  });

  it("pads a single decimal place", () => {
    expect(parseMinor("29.5")).toEqual({ ok: true, minor: 2950 });
  });

  it("tolerates spaces and commas from a pasted amount", () => {
    expect(parseMinor("24,000")).toEqual({ ok: true, minor: 2_400_000 });
    expect(parseMinor(" 24000 ")).toEqual({ ok: true, minor: 2_400_000 });
  });

  it("treats an empty field as zero, not null — every money field on this product defaults to 0", () => {
    expect(parseMinor("")).toEqual({ ok: true, minor: 0 });
  });

  it("refuses more than two decimal places rather than rounding one away", () => {
    const result = parseMinor("29.999");
    expect(result.ok).toBe(false);
  });

  it("refuses a non-numeric string", () => {
    const result = parseMinor("abc");
    expect(result.ok).toBe(false);
  });
});
