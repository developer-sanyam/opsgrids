import { describe, expect, it } from "vitest";

import { maskEmail } from "./mask";

describe("maskEmail", () => {
  it("keeps the first character of each side and hides the rest, top-level domain included", () => {
    expect(maskEmail("jane@doe.com")).toBe("j•••@d•••");
    expect(maskEmail("a.long.name+tag@subdomain.example.org")).toBe("a•••@s•••");
  });

  it("never returns any part of the address beyond those two characters", () => {
    const masked = maskEmail("priya.raman@acme-industries.example");
    expect(masked).not.toContain("riya");
    expect(masked).not.toContain("acme-");
    expect(masked).not.toContain(".example");
  });

  it("gives every address the same shape, so length is not disclosed", () => {
    expect(maskEmail("ab@cd.io")).toBe(
      maskEmail("a-much-longer-local-part@c-much-longer-domain.example"),
    );
  });

  it("masks a value with no usable @ by its tail alone", () => {
    expect(maskEmail("not-an-email")).toBe("••••••••••il");
    expect(maskEmail("@nolocal.com")).toBe("••••••••••om");
    expect(maskEmail("nodomain@")).toBe("•••••••n@");
  });

  it("does not throw on the empty string or a single character", () => {
    expect(maskEmail("")).toBe("");
    expect(maskEmail("x")).toBe("x");
  });
});
