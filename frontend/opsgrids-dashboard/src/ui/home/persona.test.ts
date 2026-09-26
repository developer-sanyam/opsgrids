import { describe, expect, it } from "vitest";

import { resolveHomePersona } from "./persona";
import type { HomePersonaSignals } from "./persona";

const BASE: HomePersonaSignals = {
  ready: true,
  ownerAccess: ["no_access", "no_access", "plan_gated"],
  canActivateProducts: false,
  employeeAccess: ["no_access", "no_access", "no_access"],
  canReadOwnApprovals: false,
};

describe("resolveHomePersona", () => {
  it("is owner while the grants fetch is not ready yet, regardless of any signal", () => {
    expect(resolveHomePersona({ ...BASE, ready: false, employeeAccess: ["ready"] })).toBe("owner");
  });

  it("is owner once any owner-level widget resolves ready", () => {
    expect(resolveHomePersona({ ...BASE, ownerAccess: ["no_access", "ready", "no_access"] })).toBe("owner");
  });

  it("is owner on core.product.activation.write:ORG alone, even with no product entitled", () => {
    expect(resolveHomePersona({ ...BASE, canActivateProducts: true })).toBe("owner");
  });

  it("is employee once an owner-level grant is entirely absent but an employee-level one is held", () => {
    expect(resolveHomePersona({ ...BASE, employeeAccess: ["ready", "no_access", "no_access"] })).toBe("employee");
  });

  it("is employee on core.approval.request.read:OWN alone — every MEMBER holds it", () => {
    expect(resolveHomePersona({ ...BASE, canReadOwnApprovals: true })).toBe("employee");
  });

  it("falls back to owner when neither an owner nor an employee signal is held", () => {
    expect(resolveHomePersona(BASE)).toBe("owner");
  });

  it("prefers owner when both an owner and an employee signal are held", () => {
    expect(
      resolveHomePersona({
        ...BASE,
        ownerAccess: ["ready", "no_access", "no_access"],
        employeeAccess: ["ready", "no_access", "no_access"],
      }),
    ).toBe("owner");
  });
});
