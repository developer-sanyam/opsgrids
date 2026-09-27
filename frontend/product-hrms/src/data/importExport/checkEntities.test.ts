/**
 * The employees entity checked by the engine's own definition lint (`checkImportEntity`): every
 * column well-formed, every lookup column served by `loadLookups`, and every sample row passing
 * the same validation an imported row gets and turning into a payload. The lookups here are built
 * the way `loadLookups` builds them, from an org-unit tree, the organisation's locations and a
 * list of employee numbers.
 */
import { checkImportEntity, makeLookup } from "@opsgrids/client";
import type { LookupSet } from "@opsgrids/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { OrgUnitNode } from "@/api/orgUnits";
import type { LocationOut } from "@/api/types";

import { departmentEntries } from "./departments";
import { employeesEntity } from "./employees";
import { locationEntries } from "./locations";

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

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

function site(code: string, name: string): LocationOut {
  return { id: `loc-${code}`, name, code, timezone: "Asia/Kolkata", country: "IN", is_active: true };
}

function lookupsFor(
  tree: readonly OrgUnitNode[],
  locations: readonly LocationOut[],
  employeeNumbers: readonly string[],
): LookupSet {
  return {
    departments: makeLookup(departmentEntries(tree)),
    locations: makeLookup(locationEntries(locations)),
    managers: makeLookup(employeeNumbers.map((label, index) => ({ label, value: `emp-${index + 1}` }))),
  };
}

describe("the employees entity definition", () => {
  it("passes the engine's definition check when the organisation has the departments, locations and managers the examples name", () => {
    const lookups = lookupsFor(
      [
        unit("d-eng", "Engineering", [unit("d-plat", "Platform"), unit("d-web", "Web")]),
        unit("d-ops", "Operations", [unit("d-ops-plat", "Platform")]),
        unit("d-hr", "Human Resources"),
        unit("d-sales", "Sales"),
      ],
      [site("BLR-HQ", "Bengaluru HQ"), site("MUM-01", "Mumbai")],
      ["EMP-0007", "EMP-0012", "EMP-0020"],
    );
    expect(checkImportEntity(employeesEntity, lookups)).toEqual([]);
  });

  it("passes when the organisation has none of the names the examples use, because the sample sheet swaps in ones it has", () => {
    const lookups = lookupsFor(
      [unit("d-fin", "Finance", [unit("d-ap", "Payables")]), unit("d-legal", "Legal")],
      [site("DEL-02", "Delhi")],
      ["A-100", "A-101"],
    );
    expect(checkImportEntity(employeesEntity, lookups)).toEqual([]);
  });

  it("passes with the lookups loadLookups builds from the tree, the locations and every page of employees", async () => {
    const directory = ["EMP-0007", "EMP-0012", "EMP-0031"].map((number, index) => ({
      id: `e-${index + 1}`,
      employee_no: number,
    }));
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string) => {
        const url = new URL(input, "http://gateway.test");
        if (url.pathname === "/api/v1/org-units") {
          return json(200, [unit("d-eng", "Engineering", [unit("d-plat", "Platform")]), unit("d-hr", "Human Resources")]);
        }
        if (url.pathname === "/api/v1/locations") return json(200, [site("BLR-HQ", "Bengaluru HQ")]);
        const page = Number(url.searchParams.get("page") ?? "1");
        const items = directory.slice((page - 1) * 2, page * 2);
        return json(200, { items, total: directory.length, page, size: 2, pages: 2 });
      }),
    );
    const lookups = await employeesEntity.loadLookups?.(new AbortController().signal);
    expect(lookups?.managers?.labels).toEqual(["EMP-0007", "EMP-0012", "EMP-0031"]);
    expect(lookups?.departments?.labels).toEqual([
      "Engineering",
      "Platform",
      "Human Resources",
      "D-ENG",
      "D-PLAT",
      "D-HR",
    ]);
    expect(lookups?.locations?.labels).toEqual(["BLR-HQ", "Bengaluru HQ"]);
    expect(checkImportEntity(employeesEntity, lookups ?? {})).toEqual([]);
  });

  it("passes when a department name is shared and so is written Parent / Child", () => {
    const lookups = lookupsFor(
      [
        unit("d-a", "Support", [unit("d-a1", "Field Ops")]),
        unit("d-b", "Sales", [unit("d-b1", "Field Ops")]),
      ],
      [site("BLR-HQ", "Bengaluru HQ")],
      ["EMP-0007"],
    );
    expect(lookups.departments?.labels).toContain("Support / Field Ops");
    expect(checkImportEntity(employeesEntity, lookups)).toEqual([]);
  });

  it("passes when two root departments share a name and are written by code", () => {
    const lookups = lookupsFor(
      [unit("d-x", "Platform"), unit("d-y", "Platform"), unit("d-hr", "Human Resources")],
      [site("BLR-HQ", "Bengaluru HQ")],
      ["EMP-0007"],
    );
    expect(lookups.departments?.labels).toEqual(["D-X", "D-Y", "Human Resources", "D-HR"]);
    expect(checkImportEntity(employeesEntity, lookups)).toEqual([]);
  });
});

describe("the employees update spec", () => {
  const update = employeesEntity.update;

  it("passes the engine's definition check together with the columns it changes", () => {
    const lookups = lookupsFor([unit("d-hr", "Human Resources")], [site("BLR-HQ", "Bengaluru HQ")], ["EMP-0007"]);
    expect(update).toBeDefined();
    expect(checkImportEntity(employeesEntity, lookups)).toEqual([]);
  });

  it("covers every importable column: each is an update field, so nothing needs a notUpdatable or extraColumns entry", () => {
    const covered = new Set([...(update?.fields.map((f) => f.column) ?? []), ...(update?.extraColumns ?? [])]);
    const importable = employeesEntity.columns.filter((c) => c.readOnly !== true && c.importOnly !== true);
    expect(importable.filter((c) => !covered.has(c.key)).map((c) => c.key)).toEqual([]);
    expect(update?.notUpdatable ?? []).toEqual([]);
    expect(update?.extraColumns ?? []).toEqual([]);
  });

  it("keeps the columns it must not change read-only, so they are ignored and never refused", () => {
    const readOnly = employeesEntity.columns.filter((c) => c.readOnly === true).map((c) => c.key);
    for (const key of ["status", "date_of_exit"]) expect(readOnly).toContain(key);
  });

  it("gives a lookup column an update field with a resolve, so a label is never compared or sent as the value", () => {
    const lookupColumns = employeesEntity.columns.filter((c) => c.lookup !== undefined).map((c) => c.key);
    for (const key of lookupColumns) {
      expect(update?.fields.find((f) => f.column === key)?.resolve, key).toBeDefined();
    }
  });
});
