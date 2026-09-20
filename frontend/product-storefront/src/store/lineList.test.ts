/**
 * The line list as arithmetic, with no DOM anywhere near it.
 *
 * Every rule the buyer can reach by pressing a button is reachable here as a function call, which
 * is the point of keeping the reducer pure: quantities, MOQ steps and totals are the part of this
 * feature that can be quietly wrong, and clicking through a card is a poor way to find out.
 */

import { describe, expect, it } from "vitest";

import { PRODUCTS } from "@/api/fixtures";
import type { Product } from "@/api/types";
import { stepQuantity } from "@/theme/render/cardOptions";
import {
  EMPTY_LINE_LIST,
  lineCount,
  lineDraft,
  lineKey,
  lineListReducer,
  lineTotalMinor,
  lineUnits,
  type LineListAction,
  type LineListState,
} from "./lineList";

/** A line built off a real fixture, so the shape stays the one the contract actually carries. */
function product(overrides: Partial<Product> = {}): Product {
  const base = PRODUCTS[0];
  if (!base) throw new Error("the product fixtures are empty");
  return { ...base, id: "line-a", name: "Amul Gold 1 L", price_min_minor: 6900, ...overrides };
}

function run(actions: LineListAction[], from: LineListState = EMPTY_LINE_LIST): LineListState {
  return actions.reduce(lineListReducer, from);
}

const butter = product();

describe("the key of a line", () => {
  it("separates two packs of the same product", () => {
    expect(lineKey("p1", "v1")).not.toBe(lineKey("p1", "v2"));
    expect(lineKey("p1", "v1")).toBe("p1:v1");
  });

  it("gives a line with no pack one key, however the caller spells the absence", () => {
    expect(lineKey("p1", null)).toBe("p1:");
  });

  it("is what the draft is filed under", () => {
    const variant = { id: "v2", label: "1 kg", price_min_minor: 12900, price_max_minor: 12900, mrp_minor: null, in_stock: true };
    const state = run([{ type: "add", line: lineDraft(butter, variant), qty: 1 }]);

    expect([...state.keys()]).toEqual([lineKey(butter.id, "v2")]);
    expect(state.get(lineKey(butter.id, "v2"))?.name).toBe("Amul Gold 1 L · 1 kg");
  });
});

describe("adding", () => {
  it("opens a line at the quantity asked for", () => {
    const state = run([{ type: "add", line: lineDraft(butter, null), qty: 1 }]);

    expect(lineCount(state)).toBe(1);
    expect(lineUnits(state)).toBe(1);
  });

  it("increments an open line rather than opening a second one", () => {
    const draft = lineDraft(butter, null);
    const state = run([
      { type: "add", line: draft, qty: 1 },
      { type: "add", line: draft, qty: 1 },
      { type: "add", line: draft, qty: 1 },
    ]);

    expect(lineCount(state)).toBe(1);
    expect(lineUnits(state)).toBe(3);
  });

  it("steps by the whole MOQ in trade and by one in retail", () => {
    const bolt = product({ id: "line-b", moq: 500 });
    expect(stepQuantity(bolt, "retail")).toBe(1);
    expect(stepQuantity(bolt, "trade")).toBe(500);

    const draft = lineDraft(bolt, null);
    const trade = run([
      { type: "add", line: draft, qty: stepQuantity(bolt, "trade") },
      { type: "add", line: draft, qty: stepQuantity(bolt, "trade") },
    ]);

    expect(lineUnits(trade)).toBe(1000);
  });

  it("ignores a press worth nothing, rather than filing a line at zero", () => {
    const state = run([{ type: "add", line: lineDraft(butter, null), qty: 0 }]);

    expect(state).toBe(EMPTY_LINE_LIST);
  });
});

describe("stepping down", () => {
  const open = run([{ type: "add", line: lineDraft(butter, null), qty: 3 }]);
  const key = lineKey(butter.id, null);

  it("lowers the quantity", () => {
    expect(lineUnits(lineListReducer(open, { type: "set-qty", key, qty: 2 }))).toBe(2);
  });

  it("removes the line at zero — a list never holds a line at nought", () => {
    const state = lineListReducer(open, { type: "set-qty", key, qty: 0 });

    expect(lineCount(state)).toBe(0);
    expect(state.has(key)).toBe(false);
  });

  it("removes the line rather than going negative, whatever the step was", () => {
    const state = lineListReducer(open, { type: "set-qty", key, qty: -500 });

    expect(lineCount(state)).toBe(0);
  });

  it("will not open a line it was never given a price for", () => {
    const state = lineListReducer(EMPTY_LINE_LIST, { type: "set-qty", key: "ghost:", qty: 4 });

    expect(state).toBe(EMPTY_LINE_LIST);
  });
});

describe("removing and clearing", () => {
  it("drops one line and keeps the rest", () => {
    const other = product({ id: "line-b", name: "Tata Salt 1 kg" });
    const state = run([
      { type: "add", line: lineDraft(butter, null), qty: 1 },
      { type: "add", line: lineDraft(other, null), qty: 1 },
      { type: "remove", key: lineKey(butter.id, null) },
    ]);

    expect([...state.values()].map((line) => line.name)).toEqual(["Tata Salt 1 kg"]);
  });

  it("empties the whole list", () => {
    const state = run([
      { type: "add", line: lineDraft(butter, null), qty: 2 },
      { type: "clear" },
    ]);

    expect(lineCount(state)).toBe(0);
    expect(lineTotalMinor(state)).toBe(0);
  });

  /* Identity is load-bearing: a press that changes nothing must not re-render the whole store. */
  it("returns the very same state when there is nothing to do", () => {
    const state = run([{ type: "add", line: lineDraft(butter, null), qty: 1 }]);

    expect(lineListReducer(state, { type: "remove", key: "ghost:" })).toBe(state);
    expect(lineListReducer(EMPTY_LINE_LIST, { type: "clear" })).toBe(EMPTY_LINE_LIST);
  });
});

describe("the total", () => {
  it("is money in minor units, never divided by anything", () => {
    const salt = product({ id: "line-b", price_min_minor: 2500 });
    const state = run([
      { type: "add", line: lineDraft(butter, null), qty: 2 },
      { type: "add", line: lineDraft(salt, null), qty: 3 },
    ]);

    expect(lineTotalMinor(state)).toBe(2 * 6900 + 3 * 2500);
  });

  it("prices a chosen pack at that pack's price, not the product's", () => {
    const kilo = { id: "v2", label: "1 kg", price_min_minor: 12900, price_max_minor: 12900, mrp_minor: null, in_stock: true };
    const state = run([{ type: "add", line: lineDraft(butter, kilo), qty: 1 }]);

    expect(lineTotalMinor(state)).toBe(12900);
  });

  it("counts lines and units as the two different things they are", () => {
    const state = run([{ type: "add", line: lineDraft(butter, null), qty: 4 }]);

    expect(lineCount(state)).toBe(1);
    expect(lineUnits(state)).toBe(4);
  });
});
