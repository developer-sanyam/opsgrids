/**
 * The Code 128 encoder against an independent one. `code128.fixtures.json` was generated with
 * `python-barcode` (`Code128(text).build()`), once for every printable ASCII character on its own
 * — which exercises all 95 subset-B symbols and the check-character arithmetic — and for a few
 * whole labels.
 */
import { describe, expect, it } from "vitest";

import fixtures from "./code128.fixtures.json";
import { Code128Error, code128Bars, code128Values, encodeCode128 } from "./code128";

const SINGLE = fixtures.single as Record<string, string>;
const MULTI = fixtures.multi as Record<string, string>;

describe("Code 128 subset B", () => {
  it("encodes every printable character exactly as the reference encoder does", () => {
    expect(Object.keys(SINGLE)).toHaveLength(95);
    for (const [char, expected] of Object.entries(SINGLE)) {
      expect(encodeCode128(char), JSON.stringify(char)).toBe(expected);
    }
  });

  it.each(Object.entries(MULTI))("encodes the label %s as the reference encoder does", (text, expected) => {
    expect(encodeCode128(text)).toBe(expected);
  });

  it("starts with the Start B symbol and ends with the stop pattern", () => {
    const modules = encodeCode128("R-1");
    expect(modules.startsWith("11010010000")).toBe(true);
    expect(modules.endsWith("1100011101011")).toBe(true);
  });

  it("computes the check character modulo 103", () => {
    // Start B (104) + 'A' (33) * 1 = 137; 137 mod 103 = 34.
    expect(code128Values("A")).toEqual([104, 33, 34]);
  });

  it("refuses what subset B cannot carry", () => {
    expect(() => encodeCode128("")).toThrow(Code128Error);
    expect(() => encodeCode128("café")).toThrow(Code128Error);
    expect(() => encodeCode128("a\tb")).toThrow(Code128Error);
  });

  it("turns modules into bar runs that cover exactly the bar modules", () => {
    const text = "FAB-001";
    const { bars, modules } = code128Bars(text);
    const rebuilt = Array.from({ length: modules }, () => "0");
    for (const [x, w] of bars) for (let i = 0; i < w; i += 1) rebuilt[x + i] = "1";
    expect(rebuilt.join("")).toBe(encodeCode128(text));
  });
});
