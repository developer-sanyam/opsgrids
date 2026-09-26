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
