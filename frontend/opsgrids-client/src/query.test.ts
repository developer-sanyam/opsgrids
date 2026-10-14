import { describe, expect, it } from "vitest";

import { paginateFixture } from "./fixtures";
import { buildQuery } from "./query";

describe("buildQuery", () => {
  it("builds a query string from the set parameters", () => {
    expect(buildQuery({ q: "acme corp", page: 2 })).toBe("?q=acme+corp&page=2");
  });

  it("is empty, not a bare question mark, when nothing is set", () => {
    expect(buildQuery({})).toBe("");
    expect(buildQuery({ q: undefined, status: "", owner: null })).toBe("");
  });

  it("sends false and zero, which are values", () => {
    expect(buildQuery({ archived: false, offset: 0 })).toBe("?archived=false&offset=0");
  });

  it("drops an empty string unless asked to keep it", () => {
    expect(buildQuery({ q: "", page: 1 })).toBe("?page=1");
    expect(buildQuery({ q: "", page: 1 }, { keepEmpty: true })).toBe("?q=&page=1");
  });

  it("never sends null or undefined, even when empty strings are kept", () => {
    expect(buildQuery({ a: null, b: undefined }, { keepEmpty: true })).toBe("");
  });
});

describe("paginateFixture", () => {
  const items = [1, 2, 3, 4, 5];

  it("slices a 1-based page and reports the envelope", () => {
    expect(paginateFixture(items, 2, 2)).toEqual({ items: [3, 4], total: 5, page: 2, size: 2, pages: 3 });
  });

  it("returns an empty slice past the last page", () => {
    expect(paginateFixture(items, 9, 2).items).toEqual([]);
  });

  it("reports zero pages for an empty list by default", () => {
    expect(paginateFixture([], 1, 25).pages).toBe(0);
  });

  it("reports one page for an empty list when asked to", () => {
    expect(paginateFixture([], 1, 25, { pagesWhenEmpty: 1 }).pages).toBe(1);
  });
});
