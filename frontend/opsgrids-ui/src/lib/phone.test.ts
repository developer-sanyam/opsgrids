import { describe, expect, it } from "vitest";

import { formatPhone, isMaskedPhone } from "./phone";

describe("formatPhone", () => {
  it("groups an Indian number as +91 NNNNN NNNNN", () => {
    expect(formatPhone("+919000000021")).toBe("+91 90000 00021");
    expect(formatPhone("+91 90000-00021")).toBe("+91 90000 00021");
  });

  it("shows any other number compactly as stored", () => {
    expect(formatPhone("+14155552671")).toBe("+14155552671");
  });

  it("never touches a masked value", () => {
    expect(formatPhone("+•• •••• ••821")).toBe("+•• •••• ••821");
    expect(formatPhone("•••••• 4821")).toBe("•••••• 4821");
    expect(formatPhone("+91 ***** **821")).toBe("+91 ***** **821");
  });

  it("returns what it cannot read unchanged, and empty for nothing", () => {
    expect(formatPhone("call reception")).toBe("call reception");
    expect(formatPhone(null)).toBe("");
    expect(formatPhone(undefined)).toBe("");
    expect(formatPhone("")).toBe("");
  });
});

describe("isMaskedPhone", () => {
  it("recognises bullets and asterisks", () => {
    expect(isMaskedPhone("•••• 1234")).toBe(true);
    expect(isMaskedPhone("***1234")).toBe(true);
    expect(isMaskedPhone("+919000000021")).toBe(false);
  });
});
