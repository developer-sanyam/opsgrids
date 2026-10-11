/**
 * The scan payload against the vectors the backend (`app/inventory/barcode.py`) is checked
 * against too — one file, two implementations, no drift.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import {
  ScanPayloadError,
  SCAN_TYPES,
  code128Encodable,
  formatPayload,
  parsePayload,
} from "./scanPayload";
import type { ScanType } from "./scanPayload";

interface Vectors {
  format: { type: ScanType; code: string; payload: string }[];
  parse: { input: string; type: ScanType | null; code: string }[];
  reject: string[];
}

const VECTORS = JSON.parse(
  // vitest runs from the package directory: frontend/product-operations -> repo root.
  readFileSync(resolve(process.cwd(), "../../libs/contracts/scan-payload-vectors.json"), "utf-8"),
) as Vectors;

describe("scan payload", () => {
  it.each(VECTORS.format)("formats $payload", ({ type, code, payload }) => {
    expect(formatPayload(type, code)).toBe(payload);
  });

  it.each(VECTORS.format)("round-trips $payload", ({ type, code }) => {
    expect(parsePayload(formatPayload(type, code))).toEqual({ type, code });
  });

  it.each(VECTORS.parse)("parses $input", ({ input, type, code }) => {
    expect(parsePayload(input)).toEqual({ type, code });
  });

  it.each(VECTORS.reject)("refuses %j", (bad) => {
    expect(() => parsePayload(bad)).toThrow(ScanPayloadError);
  });

  it("formats every type", () => {
    for (const type of SCAN_TYPES) expect(formatPayload(type, "X1")).toBe(`og:${type}:X1`);
  });

  it("cannot format an unknown type or an unprintable code", () => {
    expect(() => formatPayload("box" as ScanType, "X1")).toThrow(ScanPayloadError);
    expect(() => formatPayload("item", "café")).toThrow(ScanPayloadError);
    expect(() => formatPayload("item", "")).toThrow(ScanPayloadError);
  });

  it("knows what Code 128 subset B can carry", () => {
    expect(code128Encodable("AZaz09 ~!")).toBe(true);
    expect(code128Encodable("")).toBe(false);
    expect(code128Encodable("café")).toBe(false);
    expect(code128Encodable("a\tb")).toBe(false);
  });
});
