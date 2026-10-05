import { describe, expect, it } from "vitest";

import {
  degreesToMicro,
  formatCoordinates,
  isValidMicro,
  isValidMicroPair,
  microToDegrees,
  microToFieldText,
  parseCoordinate,
  parsePosition,
  positionOf,
} from "./geo";

describe("degreesToMicro / microToDegrees", () => {
  it("converts at the boundary and returns an integer", () => {
    expect(degreesToMicro(19.07609)).toBe(19_076_090);
    expect(degreesToMicro(-72.877656)).toBe(-72_877_656);
    expect(Number.isInteger(degreesToMicro(0.1234567))).toBe(true);
    expect(degreesToMicro(0.1234567)).toBe(123_457);
  });

  it("never returns negative zero", () => {
    expect(Object.is(degreesToMicro(-0.0000001), 0)).toBe(true);
  });

  it("round-trips a six-decimal value exactly", () => {
    for (const micro of [0, 1, -1, 19_076_090, -72_877_656, 90_000_000, -180_000_000]) {
      expect(degreesToMicro(microToDegrees(micro))).toBe(micro);
    }
  });
});

describe("microToFieldText", () => {
  it("prints six decimals, and nothing for an absent value", () => {
    expect(microToFieldText(19_076_090)).toBe("19.076090");
    expect(microToFieldText(-72_877_656)).toBe("-72.877656");
    expect(microToFieldText(null)).toBe("");
    expect(microToFieldText(undefined)).toBe("");
  });
});

describe("isValidMicro / isValidMicroPair", () => {
  it("accepts the limits and refuses anything past them, or anything not an integer", () => {
    expect(isValidMicro(90_000_000, "lat")).toBe(true);
    expect(isValidMicro(90_000_001, "lat")).toBe(false);
    expect(isValidMicro(-180_000_000, "lng")).toBe(true);
    expect(isValidMicro(180_000_001, "lng")).toBe(false);
    expect(isValidMicro(1.5, "lat")).toBe(false);
    expect(isValidMicro(Number.NaN, "lat")).toBe(false);
    expect(isValidMicro("19", "lat")).toBe(false);
    expect(isValidMicro(null, "lat")).toBe(false);
  });

  it("needs both halves of a pair", () => {
    expect(isValidMicroPair(19_000_000, 72_000_000)).toBe(true);
    expect(isValidMicroPair(19_000_000, null)).toBe(false);
    expect(isValidMicroPair(91_000_000, 72_000_000)).toBe(false);
  });
});

describe("positionOf", () => {
  it("returns a checked pair, and null for anything partial or out of range", () => {
    expect(positionOf({ lat_micro: 19_000_000, lng_micro: 72_000_000 })).toEqual({ lat_micro: 19_000_000, lng_micro: 72_000_000 });
    expect(positionOf({ lat_micro: 19_000_000, lng_micro: null })).toBeNull();
    expect(positionOf({ lat_micro: 19_000_000 })).toBeNull();
    expect(positionOf({ lat_micro: 99_000_000, lng_micro: 0 })).toBeNull();
    expect(positionOf({})).toBeNull();
  });
});

describe("parseCoordinate", () => {
  it("reads blank as empty and a number as micro-degrees", () => {
    expect(parseCoordinate("  ", "lat")).toEqual({ kind: "empty" });
    expect(parseCoordinate(" 19.07609 ", "lat")).toEqual({ kind: "value", micro: 19_076_090 });
    expect(parseCoordinate("-72.877656", "lng")).toEqual({ kind: "value", micro: -72_877_656 });
    expect(parseCoordinate("+5", "lat")).toEqual({ kind: "value", micro: 5_000_000 });
    expect(parseCoordinate(".5", "lat")).toEqual({ kind: "value", micro: 500_000 });
  });

  it("reads the typographic minus sign a coordinate carries when it is copied from a web page", () => {
    expect(parseCoordinate("−33.8688", "lat")).toEqual({ kind: "value", micro: -33_868_800 });
    expect(parseCoordinate("−−5", "lat").kind).toBe("error");
  });

  it("refuses text that is not a plain decimal", () => {
    for (const text of ["abc", "1e3", "19,07", "19.0.1", "NaN", "Infinity", "--5"]) {
      expect(parseCoordinate(text, "lat").kind).toBe("error");
    }
  });

  it("refuses an out-of-range value with the range in the sentence", () => {
    const lat = parseCoordinate("90.000001", "lat");
    expect(lat).toEqual({ kind: "error", message: "Latitude must be between -90 and 90." });
    const lng = parseCoordinate("-180.5", "lng");
    expect(lng).toEqual({ kind: "error", message: "Longitude must be between -180 and 180." });
    expect(parseCoordinate("90", "lat").kind).toBe("value");
    expect(parseCoordinate("-180", "lng").kind).toBe("value");
  });
});

describe("parsePosition", () => {
  it("treats two blank fields as no position", () => {
    expect(parsePosition("", " ")).toEqual({ kind: "none" });
  });

  it("returns the pair when both are valid", () => {
    expect(parsePosition("19.07609", "72.877656")).toEqual({
      kind: "position",
      lat_micro: 19_076_090,
      lng_micro: 72_877_656,
    });
  });

  it("refuses an unpaired value on the field that is missing", () => {
    expect(parsePosition("19.07", "")).toMatchObject({ kind: "invalid", lng: expect.stringContaining("longitude") });
    expect(parsePosition("", "72.8")).toMatchObject({ kind: "invalid", lat: expect.stringContaining("latitude") });
  });

  it("reports every bad field at once", () => {
    const result = parsePosition("95", "abc");
    expect(result.kind).toBe("invalid");
    if (result.kind === "invalid") {
      expect(result.lat).toContain("between");
      expect(result.lng).toContain("number");
    }
  });
});

describe("formatCoordinates", () => {
  it("prints degrees, with the accuracy when there is one", () => {
    expect(formatCoordinates(19_076_090, 72_877_656)).toBe("19.076090, 72.877656");
    expect(formatCoordinates(19_076_090, 72_877_656, 12)).toBe("19.076090, 72.877656 (within 12 m)");
    expect(formatCoordinates(19_076_090, 72_877_656, null)).toBe("19.076090, 72.877656");
  });
});
