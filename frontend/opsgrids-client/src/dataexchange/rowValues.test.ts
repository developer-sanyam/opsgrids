import { describe, expect, it } from "vitest";

import {
  columnOf,
  labelOfOption,
  lookupField,
  numberOf,
  optionOf,
  refuseUnlistedReferences,
  requiredTextOf,
  resolveLookup,
  resolveReference,
  sameLines,
  storedReference,
  storedText,
  textOf,
  upperCased,
} from "./rowValues";
import { RowProblem } from "./types";
import type { ImportColumn, ImportContext, Lookup, ParsedRow } from "./types";

function lookup(entries: Record<string, string>): Lookup {
  const byLabel = new Map(Object.entries(entries));
  const byId = new Map(Object.entries(entries).map(([label, id]) => [id, label]));
  return {
    resolve: (label: string) => byLabel.get(label),
    labelOf: (id: string) => byId.get(id),
    isAmbiguous: () => false,
    labels: [...byLabel.keys()],
  };
}

const COLUMN = { key: "owner", header: "Owner", lookup: "owners" } as unknown as ImportColumn;

function context(owners: Lookup | undefined): ImportContext {
  return { lookups: owners === undefined ? {} : { owners } } as unknown as ImportContext;
}

describe("cell readers", () => {
  const row: ParsedRow = { name: "Asha", blank: "", count: 3, flag: true };

  it("reads text, and null for blank, missing or non-text cells", () => {
    expect(textOf(row, "name")).toBe("Asha");
    expect(textOf(row, "blank")).toBeNull();
    expect(textOf(row, "missing")).toBeNull();
    expect(textOf(row, "count")).toBeNull();
  });

  it("makes a missing required cell one row's problem, not a crash", () => {
    expect(requiredTextOf(row, "name", "Name")).toBe("Asha");
    expect(() => requiredTextOf(row, "blank", "Name")).toThrow(RowProblem);
    expect(() => requiredTextOf(row, "blank", "Name")).toThrow("Name is required.");
  });

  it("reads a whole-number cell", () => {
    expect(numberOf(row, "count")).toBe(3);
    expect(numberOf(row, "name")).toBeNull();
  });

  it("maps an enum cell to the option's API value", () => {
    const options = [{ value: "open" as const, label: "Open" }];
    expect(optionOf({ s: "open" }, "s", options)).toBe("open");
    expect(optionOf({ s: "other" }, "s", options)).toBeNull();
    expect(labelOfOption(options, "open")).toBe("Open");
    expect(labelOfOption(options, "gone")).toBe("gone");
  });

  it("treats a stored empty string as nothing there", () => {
    expect(storedText("")).toBeNull();
    expect(storedText(null)).toBeNull();
    expect(storedText("x")).toBe("x");
  });

  it("upper-cases a currency code and leaves other values alone", () => {
    expect(upperCased("usd")).toBe("USD");
    expect(upperCased(5)).toBe(5);
  });

  it("compares multi-line text across line-break styles and trimmed ends", () => {
    expect(sameLines("a\r\nb ", "a\nb")).toBe(true);
    expect(sameLines("a", "b")).toBe(false);
    expect(sameLines(1, 1)).toBe(true);
  });
});

describe("columnOf", () => {
  it("finds a column by key and refuses a key the entity does not have", () => {
    expect(columnOf([COLUMN], "owner")).toBe(COLUMN);
    expect(() => columnOf([COLUMN], "nope")).toThrow('The entity has no column "nope"');
  });
});

describe("lookups", () => {
  const owners = lookup({ Asha: "u1", Ben: "u2" });

  it("resolves a name to its value, and refuses one the list lacks", () => {
    expect(resolveReference(owners, "Asha")).toBe("u1");
    expect(() => resolveReference(owners, "Zed")).toThrow('"Zed" is not in the list.');
    expect(() => resolveReference(undefined, "Asha")).toThrow(RowProblem);
  });

  it("compares an update with the stored id, or with the text when the list cannot find the id again", () => {
    expect(storedReference(owners, "u1")).toBe("u1");
    expect(storedReference(owners, null)).toBeNull();
    expect(storedReference(owners, "u9", "Former member")).toBe("Former member");
    expect(storedReference(owners, "u9")).toBe("u9");
  });

  it("hands on a label the list cannot resolve, as typed", () => {
    const resolve = resolveLookup(COLUMN);
    expect(resolve("Asha", context(owners))).toBe("u1");
    expect(resolve("Zed", context(owners))).toBe("Zed");
    expect(resolve(4, context(owners))).toBe(4);
  });

  it("builds an update field that reads the stored reference off the record", () => {
    const field = lookupField<{ owner: string | null }>(COLUMN, (item) => item.owner);
    expect(field.column).toBe("owner");
    expect(field.current({ owner: "u2" }, context(owners))).toBe("u2");
  });

  it("refuses an unlisted label only when the cell is a change", () => {
    const row: ParsedRow = { owner: "Zed" };
    expect(() => refuseUnlistedReferences([COLUMN], row, [], context(owners))).not.toThrow();
    expect(() => refuseUnlistedReferences([COLUMN], row, ["Owner"], context(owners))).toThrow(RowProblem);
    expect(() => refuseUnlistedReferences([COLUMN], { owner: "Asha" }, ["Owner"], context(owners))).not.toThrow();
    expect(() => refuseUnlistedReferences([COLUMN], row, ["Owner"], context(undefined))).toThrow(
      "The accepted values for Owner could not be loaded, so it cannot be checked",
    );
  });
});
