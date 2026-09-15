/**
 * The encoder's guard rail.
 *
 * A QR Code is the one thing in this design system that cannot be checked by looking at it: a
 * single wrong entry in the error-correction block tables produces a code that renders as a
 * perfectly convincing square and scans as nothing. The vectors below were diffed module for
 * module against an independent reference encoder across all 40 versions, all four levels and all
 * eight masks; what is pinned here is that agreement, so a later edit that breaks it fails loudly.
 */

import { describe, expect, it } from "vitest";

import { encodeQr, qrPathData } from "./qr";
import type { QrMatrix } from "./qr";

/** A 527-character JWS, the shape and size of a real e-invoice signed QR payload. */
const SIGNED_QR =
  "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCIsInN0dWIiOnRydWUsInBvcnRhbCI6Im5pYy1laW52b2ljZS1zdHViIn0." +  // pragma: allowlist secret
  "eyJTZWxsZXJHc3RpbiI6IjI3QUFCQ0E5MDEyRDFaNSIsIkJ1eWVyR3N0aW4iOiIyOUFBQ0NLNTY3OE0xWjkiLCJEb2NObyI6" +  // pragma: allowlist secret
  "IklOVi0yMDI2LTAxODEiLCJEb2NUeXAiOiJpbnZvaWNlIiwiRG9jRHQiOiIxOC8wOC8yMDI2IiwiVG90SW52VmFsIjoiMTAw" +  // pragma: allowlist secret
  "MzUyMC4wMCIsIkl0ZW1DbnQiOjIsIk1haW5Ic25Db2RlIjoiODcwODMwMDAiLCJJcm4iOiI5ZDRmMmMxZTlkNGYyYzFlOWQ0" +  // pragma: allowlist secret
  "ZjJjMWU5ZDRmMmMxZTlkNGYyYzFlOWQ0ZjJjMWU5ZDRmMmMxZTlkNGYyYzFlIiwiSXJuRHQiOiIyMDI2LTA4LTE4IDExOjQy" +  // pragma: allowlist secret
  "OjA3In0.3xvvlh6vua7vR1UyHWPNz6xOB8P3tWa-ghynLlb9GZY";  // pragma: allowlist secret

/** The whole version-1 matrix, written out — small enough to read, and the classic smoke test. */
const INVOICE_NUMBER_V1L = [
  "111111101011101111111",
  "100000100011001000001",
  "101110101101001011101",
  "101110101100101011101",
  "101110101001001011101",
  "100000100111101000001",
  "111111101010101111111",
  "000000000001100000000",
  "111100101111110011101",
  "011000000100110011000",
  "100111100101000001111",
  "110100000101010001000",
  "011100111010101101101",
  "000000001000110110001",
  "111111100010000010100",
  "100000100110100100110",
  "101110100011110001110",
  "101110101011110101110",
  "101110101100100101100",
  "100000101100001011001",
  "111111101010001011100",
];

function rows(matrix: QrMatrix): string[] {
  return matrix.modules.map((row) => row.map((dark) => (dark ? "1" : "0")).join(""));
}

/** FNV-1a over the modules — a compact stand-in for pinning a 117×117 matrix in the source. */
function fingerprint(matrix: QrMatrix): string {
  let hash = 0x811c9dc5;
  for (const row of matrix.modules) {
    for (const dark of row) {
      hash ^= dark ? 1 : 0;
      hash = Math.imul(hash, 0x01000193) >>> 0;
    }
  }
  return hash.toString(16).padStart(8, "0");
}

describe("encodeQr", () => {
  it("encodes a short payload into a version-1 code, module for module", () => {
    const matrix = encodeQr("INV-2026-0181", { level: "L" });
    expect(matrix.version).toBe(1);
    expect(matrix.size).toBe(21);
    expect(rows(matrix)).toEqual(INVOICE_NUMBER_V1L);
  });

  it("picks the smallest version that holds an e-invoice signed QR at each level", () => {
    // The version rises with the level because error correction is paid for in modules — worth
    // pinning, since a silently larger code is a silently less scannable one.
    expect(encodeQr(SIGNED_QR, { level: "L" }).version).toBe(16);
    expect(encodeQr(SIGNED_QR, { level: "M" }).version).toBe(18);
    expect(encodeQr(SIGNED_QR, { level: "Q" }).version).toBe(22);
    expect(encodeQr(SIGNED_QR, { level: "H" }).version).toBe(25);
  });

  it("produces the reference matrix for an e-invoice signed QR at every level", () => {
    expect(fingerprint(encodeQr(SIGNED_QR, { level: "L" }))).toBe("b7ed7832");
    expect(fingerprint(encodeQr(SIGNED_QR, { level: "M" }))).toBe("35cc7440");
    expect(fingerprint(encodeQr(SIGNED_QR, { level: "Q" }))).toBe("c756463b");
    expect(fingerprint(encodeQr(SIGNED_QR, { level: "H" }))).toBe("f237ab02");
  });

  it("encodes non-ASCII text as UTF-8 bytes", () => {
    const matrix = encodeQr("₹1,00,000 — नमस्ते", { level: "M" });
    expect(matrix.version).toBe(3);
    expect(fingerprint(matrix)).toBe("6d652af1");
  });

  it("sizes every version as 4v + 17 modules", () => {
    for (const level of ["L", "M", "Q", "H"] as const) {
      const matrix = encodeQr(SIGNED_QR, { level });
      expect(matrix.size).toBe(matrix.version * 4 + 17);
    }
  });

  it("honours a forced mask and records which one was used", () => {
    for (let mask = 0; mask < 8; mask++) {
      expect(encodeQr(SIGNED_QR, { level: "M", mask }).mask).toBe(mask);
    }
    expect(() => encodeQr("x", { mask: 8 })).toThrow(RangeError);
  });

  it("refuses a payload no version can hold, rather than truncating it", () => {
    // A truncated payload would scan perfectly into the wrong data, which is the worst outcome
    // available: silently wrong beats loudly broken only for whoever is not reading the code.
    expect(() => encodeQr("x".repeat(3000), { level: "L" })).toThrow(RangeError);
  });
});

describe("qrPathData", () => {
  it("emits one 1×1 subpath per dark module, offset by the quiet zone", () => {
    const matrix = encodeQr("INV-2026-0181", { level: "L" });
    const darkModules = matrix.modules.flat().filter(Boolean).length;
    const path = qrPathData(matrix, 4);
    expect(path.match(/M/g)?.length).toBe(darkModules);
    // The top-left finder's own top-left module, pushed out by the four-module quiet zone.
    expect(path.startsWith("M4 4h1v1h-1z")).toBe(true);
  });
});
