/**
 * The employees entity checked by the engine's own definition lint (`checkImportEntity`): every
 * column well-formed, every lookup column served by `loadLookups`, and every sample row passing
 * the same validation an imported row gets and turning into a payload. The lookups here are built
 * the way `loadLookups` builds them, from an org-unit tree and a list of employee numbers.
 */
import { checkImportEntity, makeLookup } from "@opsgrids/client";
import type { LookupSet } from "@opsgrids/client";
import { describe, expect, it } from "vitest";

import type { OrgUnitNode } from "@/api/orgUnits";

import { departmentEntries } from "./departments";
import { employeesEntity } from "./employees";

function unit(id: string, name: string, children: OrgUnitNode[] = []): OrgUnitNode {
  return {
    id,
    name,
    code: id.toUpperCase(),
    parent_id: null,
    head_membership_id: null,
    kind: "department",
    is_active: true,
    children,
  };
}

function lookupsFor(tree: readonly OrgUnitNode[], employeeNumbers: readonly string[]): LookupSet {
  return {
    departments: makeLookup(departmentEntries(tree)),
    managers: makeLookup(employeeNumbers.map((label, index) => ({ label, value: `emp-${index + 1}` }))),
  };
}

describe("the employees entity definition", () => {
  it("passes the engine's definition check when the organisation has the departments and managers the examples name", () => {
    const lookups = lookupsFor(
      [
        unit("d-eng", "Engineering", [unit("d-plat", "Platform"), unit("d-web", "Web")]),
        unit("d-ops", "Operations", [unit("d-ops-plat", "Platform")]),
        unit("d-hr", "Human Resources"),
        unit("d-sales", "Sales"),
      ],
      ["EMP-0007", "EMP-0012", "EMP-0020"],
    );
    expect(checkImportEntity(employeesEntity, lookups)).toEqual([]);
  });

  it("passes when the organisation has none of the names the examples use, because the sample sheet swaps in ones it has", () => {
    const lookups = lookupsFor(
      [unit("d-fin", "Finance", [unit("d-ap", "Payables")]), unit("d-legal", "Legal")],
      ["A-100", "A-101"],
    );
    expect(checkImportEntity(employeesEntity, lookups)).toEqual([]);
  });

  it("passes when a department name is shared and so is written Parent / Child", () => {
    const lookups = lookupsFor(
      [
        unit("d-a", "Support", [unit("d-a1", "Field Ops")]),
        unit("d-b", "Sales", [unit("d-b1", "Field Ops")]),
      ],
      ["EMP-0007"],
    );
    expect(lookups.departments?.labels).toContain("Support / Field Ops");
    expect(checkImportEntity(employeesEntity, lookups)).toEqual([]);
  });
});
