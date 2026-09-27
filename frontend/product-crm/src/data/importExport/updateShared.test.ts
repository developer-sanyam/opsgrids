/**
 * The rule every CRM update spec settles a lookup label by (`resolveLabel`): the engine's
 * ignoring-capitals match, and, for a label two entries share that way, the exact spelling.
 */
import { describe, expect, it } from "vitest";

import { RowProblem, makeLookup } from "@opsgrids/client";

import { contactImport } from "./contacts";
import { contextWith } from "./entityTestKit";
import { makeSpellingLookup } from "./shared";
import { lookupId, resolveByLookup, resolveLabel } from "./updateShared";

const CONTACTS = [
  { label: "Asha@x.co", value: "cont-1" },
  { label: "asha@x.co", value: "cont-2" },
  { label: "priya@x.co", value: "cont-3" },
];

describe("resolveLabel", () => {
  const lookup = makeSpellingLookup(CONTACTS);

  it("resolves a label that names one entry, whatever its capitals", () => {
    expect(resolveLabel(lookup, "priya@x.co")).toBe("cont-3");
    expect(resolveLabel(lookup, "  PRIYA@x.co ")).toBe("cont-3");
  });

  it("settles a label two entries share ignoring capitals by its exact spelling", () => {
    expect(lookup.isAmbiguous("asha@x.co")).toBe(true);
    expect(resolveLabel(lookup, "Asha@x.co")).toBe("cont-1");
    expect(resolveLabel(lookup, "asha@x.co")).toBe("cont-2");
    expect(resolveLabel(lookup, "  asha@x.co ")).toBe("cont-2");
  });

  it("still refuses any other spelling of a shared label, and never guesses", () => {
    expect(resolveLabel(lookup, "ASHA@x.co")).toBeUndefined();
    expect(resolveLabel(lookup, "aSha@x.co")).toBeUndefined();
  });

  it("refuses a spelling that two different entries carry, and an unknown label", () => {
    const same = makeSpellingLookup([
      { label: "Dup@x.co", value: "cont-1" },
      { label: "Dup@x.co", value: "cont-2" },
    ]);
    expect(resolveLabel(same, "Dup@x.co")).toBeUndefined();
    expect(resolveLabel(lookup, "nobody@x.co")).toBeUndefined();
  });

  it("does not turn one entry listed twice into two", () => {
    const listedTwice = makeSpellingLookup([
      { label: "Kg", value: "kg" },
      { label: "Kg", value: "kg" },
    ]);
    expect(resolveLabel(listedTwice, "Kg")).toBe("kg");
  });

  it("knows nothing of spelling in a plain lookup, where an ambiguous label stays refused", () => {
    expect(resolveLabel(makeLookup(CONTACTS), "Asha@x.co")).toBeUndefined();
  });

  it("answers exactly what the engine's own lookup answers for every label that is not shared", () => {
    for (const label of ["priya@x.co", "PRIYA@X.CO", "nobody@x.co"]) {
      expect(resolveLabel(lookup, label)).toBe(makeLookup(CONTACTS).resolve(label));
    }
  });
});

describe("lookupId and resolveByLookup", () => {
  const company = contactImport.columns.find((column) => column.key === "company");
  if (company === undefined) throw new Error("contacts has no Company column");
  const ctx = contextWith({ companies: makeSpellingLookup(CONTACTS) });

  it("returns the entry the exact spelling names", () => {
    expect(lookupId(company, "Asha@x.co", ctx)).toBe("cont-1");
    expect(resolveByLookup(company)("asha@x.co", ctx)).toBe("cont-2");
  });

  it("refuses a shared label in another spelling with the engine's own sentence, ambiguous rather than unknown", () => {
    expect(() => lookupId(company, "ASHA@x.co", ctx)).toThrow(RowProblem);
    expect(() => lookupId(company, "ASHA@x.co", ctx)).toThrow(
      "'ASHA@x.co' matches more than one of the accepted values for Company, so it cannot be chosen.",
    );
  });

  it("leaves a cell that is not text as it is", () => {
    expect(resolveByLookup(company)(null, ctx)).toBeNull();
    expect(resolveByLookup(company)(7, ctx)).toBe(7);
  });
});
