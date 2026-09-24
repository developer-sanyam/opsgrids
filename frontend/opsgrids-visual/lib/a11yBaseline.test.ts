import { describe, expect, it } from "vitest";

import { compare, countByRule, merge, serialise, totalsByRule } from "./a11yBaseline";

describe("countByRule", () => {
  it("counts offending nodes, not rules", () => {
    expect(
      countByRule([
        { id: "color-contrast", nodes: [1, 2, 3] },
        { id: "button-name", nodes: [1] },
      ]),
    ).toEqual({ "color-contrast": 3, "button-name": 1 });
  });
});

describe("compare", () => {
  it("passes a rendering no worse than its baseline", () => {
    expect(compare({ "color-contrast": 2 }, { "color-contrast": 2 })).toEqual({ regressions: [], improvements: [] });
  });

  it("fails a rule that flags more nodes than allowed", () => {
    expect(compare({ "color-contrast": 3 }, { "color-contrast": 2 }).regressions).toEqual([
      { rule: "color-contrast", allowed: 2, found: 3 },
    ]);
  });

  it("fails a rule the baseline has never seen", () => {
    expect(compare({ "button-name": 1 }, {}).regressions).toEqual([{ rule: "button-name", allowed: 0, found: 1 }]);
  });

  it("reports fewer nodes as an improvement, not a failure", () => {
    expect(compare({}, { "color-contrast": 4 })).toEqual({
      regressions: [],
      improvements: [{ rule: "color-contrast", allowed: 4, found: 0 }],
    });
  });
});

describe("merge", () => {
  const known = new Set(["a|light|375", "b|light|375", "c|light|375"]);

  it("rewrites what ran and keeps what did not", () => {
    const next = merge(
      { "a|light|375": { x: 1 }, "b|light|375": { y: 2 } },
      { "a|light|375": { x: 3 } },
      known,
    );
    expect(next).toEqual({ "a|light|375": { x: 3 }, "b|light|375": { y: 2 } });
  });

  it("drops a rendering that is now clean", () => {
    expect(merge({ "a|light|375": { x: 1 } }, { "a|light|375": {} }, known)).toEqual({});
  });

  it("drops renderings of stories or pages that no longer exist", () => {
    expect(merge({ "gone|light|375": { x: 1 } }, {}, known)).toEqual({});
  });
});

describe("serialise", () => {
  it("sorts keys and rules so the file diffs cleanly", () => {
    expect(serialise({ b: { z: 1, a: 2 }, a: { m: 1 } })).toBe(
      `${JSON.stringify({ a: { m: 1 }, b: { a: 2, z: 1 } }, null, 2)}\n`,
    );
  });
});

describe("totalsByRule", () => {
  it("sums nodes and counts renderings per rule", () => {
    expect(totalsByRule({ a: { x: 2, y: 1 }, b: { x: 3 } })).toEqual({
      x: { nodes: 5, renderings: 2 },
      y: { nodes: 1, renderings: 1 },
    });
  });
});
