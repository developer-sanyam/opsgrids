/**
 * The guard between a live answer and a screen. What matters is what it prevents — a blank page
 * from a render that throws on a shape it was not built for — and that when it refuses, it names
 * the route and what is missing.
 */

import { describe, expect, it } from "vitest";

import { ContractMismatchError, expectMembers, expectPage } from "./shape";

describe("expectMembers", () => {
  it("hands back an object that carries every member, unchanged", () => {
    const answer = { a: 1, b: 2, extra: 3 };
    expect(expectMembers("/x", answer, ["a", "b"])).toBe(answer);
  });

  it("names the route and every member that is missing", () => {
    const failure = (() => {
      try {
        expectMembers("/admin/metrics/overview", { mrr_minor: 1 }, ["mrr_minor", "mrr_series", "trial_funnel"]);
      } catch (error) {
        return error;
      }
      return null;
    })();
    expect(failure).toBeInstanceOf(ContractMismatchError);
    const mismatch = failure as ContractMismatchError;
    expect(mismatch.path).toBe("/admin/metrics/overview");
    expect(mismatch.missing).toEqual(["mrr_series", "trial_funnel"]);
    expect(mismatch.message).toContain("`mrr_series`, `trial_funnel`");
    expect(mismatch.message).toContain("out of step");
  });

  it("refuses a value that is not an object at all — nothing, a list, a string", () => {
    for (const answer of [null, undefined, [], "text", 7]) {
      expect(() => expectMembers("/x", answer, ["a"])).toThrow(ContractMismatchError);
    }
  });

  it("accepts a member that is present but empty or falsy — absent is not the same as empty", () => {
    expect(() => expectMembers("/x", { a: null, b: 0, c: "", d: [] }, ["a", "b", "c", "d"])).not.toThrow();
  });
});

describe("expectPage", () => {
  it("accepts an items list and a total", () => {
    expect(expectPage("/x", { items: [1, 2], total: 2, page: 1 })).toEqual({ items: [1, 2], total: 2, page: 1 });
    expect(expectPage("/x", { items: [], total: 0 }).items).toEqual([]);
  });

  it("refuses the console API's own list shape, which is a differently named list", () => {
    // `GET /admin/organizations` answers `{organizations, metrics}`, not a page.
    expect(() => expectPage("/admin/organizations", { organizations: [], metrics: {} })).toThrow(/`items`, `total`/);
  });

  it("refuses items that are not a list, which would throw on the first `.map`", () => {
    const failure = (() => {
      try {
        expectPage("/x", { items: "nope", total: 1 });
      } catch (error) {
        return error as ContractMismatchError;
      }
      return null;
    })();
    expect(failure?.missing).toEqual(["items (a list)"]);
  });
});
