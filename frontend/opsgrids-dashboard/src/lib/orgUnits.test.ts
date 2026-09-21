import { describe, expect, it } from "vitest";

import type { OrgUnit } from "@/api/types";
import { descendantIds, flattenOrgUnits } from "./orgUnits";

function unit(id: string, name: string, children: OrgUnit[] = []): OrgUnit {
  return { id, name, code: id.toUpperCase(), parent_id: null, head_membership_id: null, kind: "department", is_active: true, children };
}

const tree: OrgUnit[] = [
  unit("eng", "Engineering", [unit("platform", "Platform"), unit("apps", "Apps", [unit("mobile", "Mobile")])]),
  unit("sales", "Sales"),
];

describe("flattenOrgUnits", () => {
  it("walks pre-order, pairing every node with its depth", () => {
    const flat = flattenOrgUnits(tree).map(({ unit: u, depth }) => `${depth}:${u.name}`);
    expect(flat).toEqual(["0:Engineering", "1:Platform", "1:Apps", "2:Mobile", "0:Sales"]);
  });

  it("returns nothing for an empty tree", () => {
    expect(flattenOrgUnits([])).toEqual([]);
  });
});

describe("descendantIds", () => {
  it("collects every id under a node, not the node itself", () => {
    expect(descendantIds(tree, "eng")).toEqual(new Set(["platform", "apps", "mobile"]));
  });

  it("is empty for a leaf", () => {
    expect(descendantIds(tree, "platform")).toEqual(new Set());
  });

  it("is empty for an id that isn't in the tree", () => {
    expect(descendantIds(tree, "missing")).toEqual(new Set());
  });
});
