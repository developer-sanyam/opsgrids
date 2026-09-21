import { describe, expect, it } from "vitest";

import { minorToInput, parseMinor } from "./money";

describe("minorToInput", () => {
  it("renders a whole amount with no decimals", () => {
    expect(minorToInput(21_900_000)).toBe("219000");
  });

  it("renders cents padded to two digits", () => {
    expect(minorToInput(2950)).toBe("29.50");
    expect(minorToInput(105)).toBe("1.05");
  });

  it("renders zero as a bare 0", () => {
    expect(minorToInput(0)).toBe("0");
  });
});

describe("parseMinor", () => {
  it("parses a whole-number amount", () => {
    expect(parseMinor("219000")).toEqual({ ok: true, minor: 21_900_000 });
  });

  it("parses two decimal places without float drift", () => {
    expect(parseMinor("29.35")).toEqual({ ok: true, minor: 2935 });
  });

  it("tolerates spaces and commas", () => {
    expect(parseMinor("2,19,000")).toEqual({ ok: true, minor: 21_900_000 });
  });

  it("treats an empty string as zero", () => {
    expect(parseMinor("")).toEqual({ ok: true, minor: 0 });
  });

  it("refuses more than two decimal places", () => {
    const result = parseMinor("29.359");
    expect(result.ok).toBe(false);
  });

  it("refuses a non-numeric amount", () => {
    const result = parseMinor("nope");
    expect(result.ok).toBe(false);
  });
});
