/**
 * The ladder, pinned.
 *
 * This mirror is only ever allowed to be *narrower* than the server, never wider: a control it
 * hides wrongly is an annoyance, and a control it shows wrongly is a form the user fills in and
 * is then refused. The two rules below are the ones that have actually gone wrong in systems like
 * this — checking a permission name without its scope, and letting `read` stand in for
 * `read_sensitive`.
 */

import { describe, expect, it } from "vitest";

import { hasPermission, parseGrant, scopeFor, scopeRank } from "./scope";

describe("scopeRank", () => {
  it("orders OWN < TEAM < LOCATION < ORG", () => {
    expect(scopeRank("OWN")).toBeLessThan(scopeRank("TEAM"));
    expect(scopeRank("TEAM")).toBeLessThan(scopeRank("LOCATION"));
    expect(scopeRank("LOCATION")).toBeLessThan(scopeRank("ORG"));
  });

  it("does not recognise an invented scope", () => {
    expect(scopeRank("EVERYTHING")).toBe(-1);
  });
});

describe("parseGrant", () => {
  it("splits the scope off the code", () => {
    expect(parseGrant("hrms.people.employee.read:ORG")).toEqual({
      code: "hrms.people.employee.read",
      scope: "ORG",
    });
  });

  it("reads a scopeless grant as the narrowest one", () => {
    expect(parseGrant("core.approval.request.decide")?.scope).toBe("OWN");
  });

  it("reads an unrecognised scope as the narrowest one", () => {
    expect(parseGrant("core.approval.request.decide:GLOBAL")?.scope).toBe("OWN");
  });
});

describe("hasPermission", () => {
  const grants = [
    "core.approval.request.decide:OWN",
    "core.approval.delegation.write:OWN",
    "hrms.people.employee.read:ORG",
  ];

  it("grants a wider scope than asked for", () => {
    expect(hasPermission(grants, "hrms.people.employee.read", "TEAM")).toBe(true);
  });

  it("refuses a name held only at a narrower scope than the endpoint floors at", () => {
    // Everyone holds decide:OWN; the unassigned queue needs ORG. Checking the name alone here
    // would show the org-wide control to every employee in the organization.
    expect(hasPermission(grants, "core.approval.request.decide", "ORG")).toBe(false);
    expect(hasPermission(grants, "core.approval.request.decide")).toBe(true);
  });

  it("never lets read stand in for read_sensitive", () => {
    expect(hasPermission(["hrms.people.employee.read:ORG"], "hrms.people.employee.read_sensitive")).toBe(
      false,
    );
  });

  it("matches codes exactly — no prefixes, no wildcards", () => {
    expect(hasPermission(["core.approval.request:ORG"], "core.approval.request.decide")).toBe(false);
    expect(hasPermission(["*:ORG"], "core.approval.request.decide")).toBe(false);
  });

  it("refuses when the caller holds nothing", () => {
    expect(hasPermission([], "core.approval.request.decide")).toBe(false);
  });
});

describe("scopeFor", () => {
  it("reports the widest scope held, so a screen can explain what it can see", () => {
    expect(
      scopeFor(
        ["core.approval.request.read:OWN", "core.approval.request.read:ORG"],
        "core.approval.request.read",
      ),
    ).toBe("ORG");
  });

  it("is null for a permission the caller does not hold", () => {
    expect(scopeFor([], "core.approval.policy.write")).toBeNull();
  });
});
