/**
 * The assets entity checked by the engine's own definition lint (`checkImportEntity`): every
 * column well-formed, cost tied to a currency column, and every sample row passing the same
 * validation an imported row gets and turning into a payload. Assets have no reference data, so
 * `loadLookups` provides none and the check runs with an empty set.
 */
import { checkImportEntity } from "@opsgrids/client";
import { describe, expect, it } from "vitest";

import { assetsEntity } from "./assets";

describe("the assets entity definition", () => {
  it("passes the engine's definition check", () => {
    expect(checkImportEntity(assetsEntity, {})).toEqual([]);
  });

  it("names no lookup, so nothing is left for loadLookups to provide", () => {
    expect(assetsEntity.columns.filter((column) => column.lookup !== undefined)).toEqual([]);
    expect(assetsEntity.loadLookups).toBeUndefined();
  });
});

describe("the assets update spec", () => {
  const update = assetsEntity.update;

  it("is there, and is what the engine's definition check lints", () => {
    expect(update).toBeDefined();
    expect(checkImportEntity(assetsEntity, {})).toEqual([]);
  });

  it("covers every importable column: each is an update field, so nothing needs a notUpdatable or extraColumns entry", () => {
    const covered = new Set([...(update?.fields.map((f) => f.column) ?? []), ...(update?.extraColumns ?? [])]);
    const importable = assetsEntity.columns.filter((c) => c.readOnly !== true && c.importOnly !== true);
    expect(importable.filter((c) => !covered.has(c.key)).map((c) => c.key)).toEqual([]);
    expect(update?.notUpdatable ?? []).toEqual([]);
    expect(update?.extraColumns ?? []).toEqual([]);
  });

  it("matches on the tag alone: it is the key column and there is no Record ID", () => {
    expect(update?.keyColumn).toBe("tag");
    expect(assetsEntity.recordIdKey).toBeUndefined();
  });
});
