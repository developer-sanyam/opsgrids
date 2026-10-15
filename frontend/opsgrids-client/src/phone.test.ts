import { afterEach, describe, expect, it } from "vitest";

import { getDefaultPhoneRegion, normalisePhone, setDefaultPhoneRegion } from "./phone";

afterEach(() => setDefaultPhoneRegion("IN"));

describe("normalisePhone", () => {
  it("keeps a number that already has a country code, minus the separators", () => {
    expect(normalisePhone("+91 90000-00012")).toBe("+919000000012");
    expect(normalisePhone("+1 (415) 555.2671")).toBe("+14155552671");
    expect(normalisePhone("0091 90000 00012")).toBe("+919000000012");
  });

  describe("in an Indian organization", () => {
    it.each([
      ["9000000012", "+919000000012"],
      ["90000 00012", "+919000000012"],
      ["09000000012", "+919000000012"],
      ["919000000012", "+919000000012"],
      ["6000000001", "+916000000001"],
    ])("completes %s to %s", (raw, expected) => {
      expect(normalisePhone(raw, "IN")).toBe(expected);
    });

    it.each(["5000000012", "900000001", "90000000123", "4155552671", "abc", "", "  ", "+0123456789"])(
      "refuses %j",
      (raw) => {
        expect(normalisePhone(raw, "IN")).toBeNull();
      },
    );
  });

  it("never completes a bare number for any other country, or for none", () => {
    expect(normalisePhone("9000000012", "GB")).toBeNull();
    expect(normalisePhone("9000000012", null)).toBeNull();
    expect(normalisePhone("+919000000012", null)).toBe("+919000000012");
  });

  it("defaults to the organization's region once it is set", () => {
    expect(getDefaultPhoneRegion()).toBe("IN");
    setDefaultPhoneRegion("us");
    expect(getDefaultPhoneRegion()).toBe("US");
    expect(normalisePhone("9000000012")).toBeNull();
    setDefaultPhoneRegion(null);
    expect(getDefaultPhoneRegion()).toBeNull();
  });
});
