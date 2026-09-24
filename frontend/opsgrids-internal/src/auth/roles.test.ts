/**
 * The role matrix, as the console renders it, held to ADR-0003's addendum table. It hides UI and
 * the server refuses regardless — so the table matters where it is wrong in the *other* direction
 * too: an entry the operator is shown that the server will refuse is a dead end, and one they are
 * not shown that the server would allow is a screen nobody can reach.
 */

import { describe, expect, it } from "vitest";

import { canDo, roleText, rolesFor } from "./roles";
import type { Capability } from "./roles";

/** The addendum's table, row by row: which of owner / billing_ops / support may do each thing. */
const ADDENDUM: Record<Capability, { owner: boolean; billing_ops: boolean; support: boolean }> = {
  organizations: { owner: true, billing_ops: true, support: true },
  metrics: { owner: true, billing_ops: true, support: false },
  audit: { owner: true, billing_ops: false, support: true },
  subscriptions: { owner: true, billing_ops: true, support: false },
  usage: { owner: true, billing_ops: true, support: false },
  overrides: { owner: true, billing_ops: true, support: false },
  creditLedger: { owner: true, billing_ops: true, support: false },
  // The Plans screen: catalog writes are owner-only server-side, and the screen admits the roles
  // that run pricing overrides — see roles.ts.
  catalog: { owner: true, billing_ops: true, support: false },
};

describe("canDo", () => {
  for (const [capability, expected] of Object.entries(ADDENDUM) as [
    Capability,
    { owner: boolean; billing_ops: boolean; support: boolean },
  ][]) {
    it(`${capability}: owner ${expected.owner}, billing_ops ${expected.billing_ops}, support ${expected.support}`, () => {
      expect(canDo(["owner"], capability)).toBe(expected.owner);
      expect(canDo(["billing_ops"], capability)).toBe(expected.billing_ops);
      expect(canDo(["support"], capability)).toBe(expected.support);
    });
  }

  it("names owner in every row — owner is every call site accepting it, not a stored list", () => {
    for (const capability of Object.keys(ADDENDUM) as Capability[]) {
      expect(rolesFor(capability), capability).toContain("owner");
    }
  });

  it("admits an operator holding any one of several roles", () => {
    expect(canDo(["support", "billing_ops"], "subscriptions")).toBe(true);
    expect(canDo(["support", "billing_ops"], "audit")).toBe(true);
  });

  it("admits nothing to a role it has never heard of, or to no role at all", () => {
    expect(canDo(["revenue_ops"], "organizations")).toBe(false);
    expect(canDo([], "organizations")).toBe(false);
  });
});

describe("roleText", () => {
  it("reads the roles as a person would, in one stable order", () => {
    expect(roleText(["support", "owner"])).toBe("Owner · Support");
    expect(roleText(["billing_ops"])).toBe("Billing ops");
  });

  it("keeps a role it does not recognise rather than dropping it", () => {
    expect(roleText(["owner", "auditor"])).toBe("Owner · auditor");
  });

  it("falls back to a neutral word for no roles", () => {
    expect(roleText([])).toBe("Operator");
  });
});
