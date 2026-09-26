import { describe, expect, it } from "vitest";

import { checkImportEntity } from "./checkEntity";
import { makeLookup } from "./lookups";
import { makeProductEntity, productColumns, productLookups } from "./testkit";
import type { ImportColumn, SheetValue } from "./types";
import { RowProblem } from "./types";

const sku: ImportColumn = { key: "sku", header: "SKU", kind: "text", required: true, help: "The sku." };
const name: ImportColumn = { key: "name", header: "Name", kind: "text", required: true, help: "The name." };
const samples: readonly Record<string, SheetValue>[] = [{ sku: "A-1", name: "Bracket" }];

function problemsFor(
  columns: readonly ImportColumn[],
  sampleRows: readonly Record<string, SheetValue>[] = samples,
  lookups = productLookups,
): string[] {
  return checkImportEntity(makeProductEntity({ columns, sampleRows }), lookups);
}

describe("checkImportEntity", () => {
  it("finds nothing wrong with a sound entity", () => {
    expect(checkImportEntity(makeProductEntity(), productLookups)).toEqual([]);
  });

  it("flags columns that share a key", () => {
    expect(problemsFor([sku, { ...name, key: "sku" }])).toContain('More than one column has the key "sku"');
  });

  it("flags headers that would be matched as the same, ignoring case, spacing and an asterisk", () => {
    const problems = problemsFor([sku, { ...name, key: "other", header: " s k u * " }]);
    expect(problems.some((p) => p.includes('More than one column has the header "sku"'))).toBe(true);
  });

  it("flags a read-only column whose header clashes with an importable one", () => {
    const problems = problemsFor([sku, { key: "code", header: "sku", kind: "text", help: "Copy.", readOnly: true }]);
    expect(problems.some((p) => p.includes("More than one column has the header"))).toBe(true);
  });

  it("flags an enum with no options, and options on a column that is not an enum", () => {
    expect(problemsFor([sku, { key: "s", header: "Status", kind: "enum", help: "Status." }])).toContain(
      'Column "s" is an enum with no options',
    );
    expect(problemsFor([sku, { ...name, key: "n", header: "N", options: [{ value: "a", label: "A" }] }])).toContain(
      'Column "n" has options but is not an enum',
    );
  });

  it("flags an option spelling that two options would both claim", () => {
    const problems = problemsFor([
      sku,
      {
        key: "s",
        header: "Status",
        kind: "enum",
        help: "Status.",
        options: [
          { value: "open", label: "Open" },
          { value: "closed", label: "open" },
        ],
      },
    ]);
    expect(problems).toContain('Column "s" accepts "open" for two different options');
  });

  it("flags a lookup column that loadLookups does not provide", () => {
    const problems = problemsFor([sku, { key: "w", header: "Warehouse", kind: "text", help: "W.", lookup: "warehouses" }], [
      { sku: "A" },
    ]);
    expect(problems).toContain('Column "w" uses lookup "warehouses", which loadLookups does not provide');
  });

  it("flags a lookup column that is not text", () => {
    const problems = problemsFor([sku, { key: "w", header: "W", kind: "integer", help: "W.", lookup: "categories" }], [
      { sku: "A", w: 1 },
    ]);
    expect(problems).toContain('Column "w" uses a lookup, so its kind must be "text"');
  });

  it("flags money with no currency column and no default currency", () => {
    const problems = problemsFor([sku, { key: "price", header: "Price", kind: "money", help: "Price." }]);
    expect(problems.some((p) => p.includes('there is no "currency" currency column'))).toBe(true);
  });

  it("accepts money with an explicit default currency and no currency column", () => {
    expect(problemsFor([sku, { key: "price", header: "Price", kind: "money", help: "Price.", defaultCurrency: "INR" }], [
      { sku: "A", price: "10" },
    ])).toEqual([]);
  });

  it("follows currencyKey to the currency column, and flags one that is missing or read-only", () => {
    const money: ImportColumn = { key: "price", header: "Price", kind: "money", help: "Price.", currencyKey: "ccy" };
    const holder: ImportColumn = { key: "ccy", header: "Ccy", kind: "text", help: "Currency.", defaultCurrency: "INR" };
    expect(problemsFor([sku, money, holder], [{ sku: "A", price: "10", ccy: "INR" }])).toEqual([]);
    expect(problemsFor([sku, money])).toEqual(expect.arrayContaining([expect.stringContaining('no "ccy" currency column')]));
    expect(problemsFor([sku, money, { ...holder, readOnly: true }])).toEqual(
      expect.arrayContaining([expect.stringContaining("read-only and ignored on import")]),
    );
  });

  it("flags a column with no help, a pattern with no hint, and bounds that cannot both hold", () => {
    const problems = problemsFor([
      sku,
      { key: "a", header: "A", kind: "text", help: "  " },
      { key: "b", header: "B", kind: "text", help: "B.", pattern: /^x$/ },
      { key: "c", header: "C", kind: "integer", help: "C.", min: 5, max: 1 },
    ]);
    expect(problems).toContain('Column "a" has no help text for the Instructions sheet');
    expect(problems).toContain('Column "b" has a pattern but no patternHint, so a user cannot tell what is expected');
    expect(problems).toContain('Column "c" has min above max');
  });

  it("flags an entity with no columns or no sample rows", () => {
    expect(checkImportEntity(makeProductEntity({ columns: [], sampleRows: [] }), productLookups)).toEqual(
      expect.arrayContaining(["The entity has no columns", "There are no sample rows for the sample sheet"]),
    );
  });
});

describe("checkImportEntity: the sample rows", () => {
  it("flags a key that is no column", () => {
    expect(problemsFor([sku, name], [{ sku: "A", name: "B", colour: "red" }])).toContain(
      'Sample row 1 has the unknown key "colour"',
    );
  });

  it("flags a value for a read-only column, which the sample sheet would not carry", () => {
    const problems = problemsFor(
      [sku, name, { key: "id", header: "Id", kind: "text", help: "Id.", readOnly: true }],
      [{ sku: "A", name: "B", id: "p-1" }],
    );
    expect(problems).toContain('Sample row 1 has a value for "id", which is read-only');
  });

  it("flags a missing required value", () => {
    const problems = problemsFor([sku, name], [{ sku: "A" }, { sku: "B", name: "  " }]);
    expect(problems).toContain('Sample row 1 has no value for required "name"');
    expect(problems).toContain('Sample row 2 has no value for required "name"');
  });

  it("flags a sample row that fails the entity's own validation, naming the row and the reason", () => {
    const problems = problemsFor(
      [sku, { key: "qty", header: "Quantity", kind: "integer", help: "Qty.", min: 0 }],
      [{ sku: "A", qty: 5 }, { sku: "B", qty: -1 }],
    );
    expect(problems).toEqual(["Sample row 2 does not pass its own validation: Quantity must be at least 0"]);
  });

  it("accepts a sample row that names something the organisation lacks: the sample sheet swaps in one it has", () => {
    const other = { categories: makeLookup([{ label: "Paint", value: "c" }]) };
    expect(problemsFor(productColumns, [{ sku: "A", name: "B", category: "Tools" }], other)).toEqual([]);
  });

  it("flags a sample row that names a value when the lookup is empty, since nothing can stand in", () => {
    const problems = problemsFor(productColumns, [{ sku: "A", name: "B", category: "Tools" }], {
      categories: makeLookup([]),
    });
    expect(problems).toEqual([
      "Sample row 1 does not pass its own validation: 'Tools' is not in the list of Categories (see the Lists sheet)",
    ]);
  });

  it("flags a column that is both read-only and import-only", () => {
    const problems = problemsFor([sku, { key: "x", header: "X", kind: "text", help: "X.", readOnly: true, importOnly: true }]);
    expect(problems).toContain('Column "x" is both readOnly and importOnly, so it would appear nowhere');
  });

  it("accepts an import-only column and a sample value for it", () => {
    const opening: ImportColumn = { key: "opening", header: "Opening stock", kind: "integer", help: "Units.", importOnly: true };
    expect(problemsFor([sku, opening], [{ sku: "A", opening: 5 }])).toEqual([]);
  });

  it("flags a sample row that cannot be turned into a payload", () => {
    const entity = makeProductEntity({ columns: [sku, name], sampleRows: samples });
    const throwing = {
      ...entity,
      toPayload: () => {
        throw new RowProblem("Company 'Acme' not found");
      },
    };
    expect(checkImportEntity(throwing, productLookups)).toEqual([
      "Sample row 1 cannot be turned into a payload: Company 'Acme' not found",
    ]);
  });

  it("does not repeat a payload problem when the row already failed validation", () => {
    const entity = makeProductEntity({ columns: [sku, name], sampleRows: [{ sku: "A" }] });
    const throwing = {
      ...entity,
      toPayload: () => {
        throw new RowProblem("never reached");
      },
    };
    const problems = checkImportEntity(throwing, productLookups);
    expect(problems.some((p) => p.includes("never reached"))).toBe(false);
  });
});
