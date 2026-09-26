/**
 * The units of measure an import may name. The server folds the spellings people actually type
 * ("Nos", "Kgs", "Ltr", "Mtr") onto the registry unit before it looks the unit up, so a sheet
 * that says "Nos" lands on `pcs` without minting a unit of its own. The lookup therefore accepts
 * those spellings too, or a sheet exported from Tally would be refused row by row for units the
 * server would have taken.
 */
import { makeLookup } from "@opsgrids/client";
import type { Lookup } from "@opsgrids/client";

import type { UomOut } from "@/api/types";

/**
 * Spellings the server folds onto a registry unit, each mapped to that unit's code. Copied from
 * `_ALIASES` in `services/operations/app/inventory/quantity.py`, which is the source: change them
 * together (`units.test.ts` reads that file and fails when the two differ). The server matches
 * them ignoring case, as the lookup does. Only these fold: a unit's full name ("Kilogram") is not
 * one, and the server would create a unit of that name.
 */
export const UNIT_ALIASES: Readonly<Record<string, string>> = {
  pc: "pcs",
  piece: "pcs",
  pieces: "pcs",
  nos: "pcs",
  ea: "pcs",
  each: "pcs",
  units: "pcs",
  meter: "m",
  meters: "m",
  metre: "m",
  metres: "m",
  mtr: "m",
  kgs: "kg",
  gm: "g",
  gms: "g",
  grams: "g",
  litre: "l",
  liter: "l",
  ltr: "l",
  boxes: "box",
  cartons: "carton",
  bundles: "bundle",
  sets: "set",
  rolls: "roll",
};

/** A unit's spelling in the sheet and in the payload. A registry unit is its code (`pcs`, `kg`,
 *  `l`); a custom unit keeps the spelling it was first created with (`MT`), which the API folds
 *  back onto the same unit. */
export function unitLabel(unit: UomOut): string {
  return unit.is_system ? unit.code : unit.name;
}

/**
 * The organisation's units as an import lookup. `labels`, which the Lists sheet and the dropdown
 * show, holds only the units that exist. `resolve` also takes the folded spellings, each giving
 * the registry unit's own label, so `Nos` in a sheet is sent as `pcs`. A spelling a unit already
 * uses is left to that unit, and one whose registry unit the organisation does not have is not
 * offered at all.
 */
export function makeUnitLookup(units: readonly UomOut[]): Lookup {
  const own = units.map((unit) => ({ label: unitLabel(unit), value: unitLabel(unit) }));
  const known = makeLookup(own);
  const folded = Object.entries(UNIT_ALIASES).flatMap(([alias, code]) => {
    const target = units.find((unit) => unit.is_system && unit.code === code);
    const taken = known.resolve(alias) !== undefined || known.isAmbiguous(alias);
    return target === undefined || taken ? [] : [{ label: alias, value: unitLabel(target) }];
  });
  const lookup = makeLookup([...own, ...folded]);
  return { ...lookup, labels: known.labels };
}

/**
 * The label an export writes for the unit an item was stored with. An item keeps the spelling it
 * was created with ("Nos", "KGS"), so the raw value would re-import only by way of the folding;
 * the canonical label ("pcs", "kg") is what the Lists sheet shows. A spelling that resolves to no
 * unit is written as it is stored.
 */
export function canonicalUnitLabel(units: Lookup | undefined, stored: string): string {
  const value = units?.resolve(stored);
  if (units === undefined || value === undefined) return stored;
  return units.labelOf(value) ?? stored;
}
