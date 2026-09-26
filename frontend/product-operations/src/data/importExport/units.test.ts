import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import type { UomOut } from "@/api/types";
import { UNIT_ALIASES, canonicalUnitLabel, makeUnitLookup, unitLabel } from "./units";

/** The server's unit registry and alias table: the source `UNIT_ALIASES` is copied from. */
const QUANTITY_PY = resolve(
  import.meta.dirname,
  "../../../../../services/operations/app/inventory/quantity.py",
);

/** The block of `quantity.py` that follows `marker` up to the closing bracket. */
function blockAfter(source: string, marker: RegExp, close: string): string {
  const match = marker.exec(source);
  if (match === null) throw new Error(`quantity.py no longer has ${String(marker)}; update this test`);
  const start = match.index + match[0].length;
  return source.slice(start, source.indexOf(close, start));
}

/** The server's alias table and the codes of the registry it seeds every organisation with. */
function serverUnits(): { aliases: Record<string, string>; registry: string[] } {
  const source = readFileSync(QUANTITY_PY, "utf8");
  const aliases: Record<string, string> = {};
  for (const [, alias, code] of blockAfter(source, /_ALIASES = \{/, "}").matchAll(/"([^"]+)":\s*"([^"]+)"/g)) {
    if (alias !== undefined && code !== undefined) aliases[alias] = code;
  }
  const registry = [...blockAfter(source, /UNITS: dict\[str, Unit\] = _units\(/, "\n)\n").matchAll(/Unit\("([^"]+)"/g)]
    .map((match) => match[1])
    .filter((code): code is string => code !== undefined);
  return { aliases, registry };
}

function unit(code: string, name: string, isSystem: boolean): UomOut {
  return { id: `uom-${code}`, code, name, dimension: "count", is_discrete: false, is_system: isSystem };
}

function seededOrganisation(registry: readonly string[]): UomOut[] {
  return registry.map((code) => unit(code, code.toUpperCase(), true));
}

describe("the unit aliases", () => {
  it("are the server's own, spelling for spelling (services/operations/app/inventory/quantity.py)", () => {
    const { aliases } = serverUnits();
    expect(Object.keys(aliases).length).toBeGreaterThan(10);
    expect(UNIT_ALIASES).toEqual(aliases);
  });

  it("each fold onto a unit the seed registry contains", () => {
    const { registry } = serverUnits();
    expect(registry).toEqual(expect.arrayContaining(["pcs", "kg", "l", "m", "g"]));
    for (const [alias, code] of Object.entries(UNIT_ALIASES)) {
      expect(registry, `${alias} folds onto ${code}, which the registry does not have`).toContain(code);
    }
  });

  it("all resolve, in an organisation that has the whole registry, to the registry unit's own label", () => {
    const lookup = makeUnitLookup(seededOrganisation(serverUnits().registry));
    for (const [alias, code] of Object.entries(UNIT_ALIASES)) {
      expect(lookup.resolve(alias), alias).toBe(code);
      expect(lookup.resolve(alias.toUpperCase()), alias).toBe(code);
      expect(lookup.isAmbiguous(alias), alias).toBe(false);
    }
  });
});

describe("makeUnitLookup", () => {
  const ORGANISATION: UomOut[] = [
    unit("pcs", "Piece", true),
    unit("kg", "Kilogram", true),
    unit("l", "Litre", true),
    unit("m", "Metre", true),
    unit("mt", "MT", false),
  ];

  it("reads the Tally spellings as the units they fold onto", () => {
    const lookup = makeUnitLookup(ORGANISATION);
    expect(lookup.resolve("Nos")).toBe("pcs");
    expect(lookup.resolve("Kgs")).toBe("kg");
    expect(lookup.resolve("Ltr")).toBe("l");
    expect(lookup.resolve("Pcs")).toBe("pcs");
    expect(lookup.resolve("Mtr")).toBe("m");
    expect(lookup.resolve("  nos ")).toBe("pcs");
  });

  it("lists only the units that exist, so the Lists sheet and the dropdown do not show spellings", () => {
    const lookup = makeUnitLookup(ORGANISATION);
    expect(lookup.labels).toEqual(["pcs", "kg", "l", "m", "MT"]);
  });

  it("keeps a custom unit under the spelling it was created with, and still refuses a name the server would not fold", () => {
    const lookup = makeUnitLookup(ORGANISATION);
    expect(lookup.resolve("mt")).toBe("MT");
    expect(lookup.resolve("Kilogram")).toBeUndefined();
    expect(lookup.resolve("Piece")).toBe("pcs");
    expect(lookup.resolve("furlongs")).toBeUndefined();
    expect(lookup.isAmbiguous("furlongs")).toBe(false);
  });

  it("does not offer a spelling whose registry unit the organisation does not have", () => {
    const lookup = makeUnitLookup([unit("kg", "Kilogram", true)]);
    expect(lookup.resolve("Nos")).toBeUndefined();
    expect(lookup.resolve("Kgs")).toBe("kg");
  });

  it("leaves a spelling to the unit that already uses it, instead of calling it ambiguous", () => {
    const lookup = makeUnitLookup([unit("pcs", "Piece", true), unit("nos", "Nos", false)]);
    expect(lookup.resolve("Nos")).toBe("Nos");
    expect(lookup.isAmbiguous("Nos")).toBe(false);
    expect(lookup.labels).toEqual(["pcs", "Nos"]);
  });

  it("gives the label of a unit from its value, never from a spelling", () => {
    const lookup = makeUnitLookup(ORGANISATION);
    expect(lookup.labelOf("pcs")).toBe("pcs");
    expect(lookup.labelOf("MT")).toBe("MT");
    expect(lookup.labelOf("nos")).toBeUndefined();
  });
});

describe("unitLabel", () => {
  it("is the code for a registry unit and the created spelling for a custom one", () => {
    expect(unitLabel(unit("kg", "Kilogram", true))).toBe("kg");
    expect(unitLabel(unit("mt", "MT", false))).toBe("MT");
  });
});

describe("canonicalUnitLabel", () => {
  const lookup = makeUnitLookup([
    unit("pcs", "Piece", true),
    unit("kg", "Kilogram", true),
    unit("mt", "MT", false),
  ]);

  it("writes the label the Lists sheet shows for whatever spelling the item was stored with", () => {
    expect(canonicalUnitLabel(lookup, "Nos")).toBe("pcs");
    expect(canonicalUnitLabel(lookup, "KGS")).toBe("kg");
    expect(canonicalUnitLabel(lookup, "PCS")).toBe("pcs");
    expect(canonicalUnitLabel(lookup, "mt")).toBe("MT");
    expect(canonicalUnitLabel(lookup, "kg")).toBe("kg");
  });

  it("writes a unit no label reaches as it is stored, and does not need the lookup", () => {
    expect(canonicalUnitLabel(lookup, "furlongs")).toBe("furlongs");
    expect(canonicalUnitLabel(undefined, "Nos")).toBe("Nos");
  });
});
