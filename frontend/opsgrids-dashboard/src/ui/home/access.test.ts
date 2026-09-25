import { describe, expect, it } from "vitest";

import { resolveAccess } from "./access";

describe("resolveAccess", () => {
  it("is plan_gated when the product is not entitled, regardless of any grant", () => {
    expect(resolveAccess(false, true)).toBe("plan_gated");
    expect(resolveAccess(false, false)).toBe("plan_gated");
  });

  it("is no_access when the product is entitled but the caller holds no grant", () => {
    expect(resolveAccess(true, false)).toBe("no_access");
  });

  it("is ready only once both are true", () => {
    expect(resolveAccess(true, true)).toBe("ready");
  });
});
