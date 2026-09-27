import { describe, expect, it, vi } from "vitest";

import { makeLookup } from "./lookups";
import { buildUpdate, indexExisting, isClearToken, matchRow, normaliseKey, planUpdate } from "./update";
import { RowProblem } from "./types";
import type { ImportColumn, ImportContext, UpdateSpec } from "./types";

interface Stored {
  id: string;
  sku: string;
  name: string;
  industry: string | null;
  price: number;
  active: boolean;
  owner: string | null;
}

const columns: readonly ImportColumn[] = [
  { key: "sku", header: "SKU", kind: "text", help: "" },
  { key: "name", header: "Name", kind: "text", help: "" },
  { key: "industry", header: "Industry", kind: "text", help: "" },
  { key: "price", header: "Price", kind: "money", help: "" },
  { key: "active", header: "Active", kind: "boolean", help: "" },
  { key: "owner", header: "Owner", kind: "text", help: "", lookup: "owners" },
];

const ctx: ImportContext = {
  lookups: { owners: makeLookup([{ label: "Asha", value: "own-1" }, { label: "Ravi", value: "own-2" }]) },
  signal: new AbortController().signal,
};

const stored: Stored = {
  id: "id-1",
  sku: "AB-1",
  name: "Bracket",
  industry: "Steel",
  price: 1250,
  active: true,
  owner: "own-1",
};

const send = vi.fn<(existing: Stored, body: Readonly<Record<string, unknown>>) => Promise<void>>(() => Promise.resolve());

const spec: UpdateSpec<Stored> = {
  loadExisting: () => Promise.resolve([stored]),
  idOf: (s) => s.id,
  keyColumn: "sku",
  keyOf: (s) => s.sku,
  fields: [
    { column: "sku", current: (s) => s.sku, immutable: true },
    { column: "name", current: (s) => s.name },
    { column: "industry", current: (s) => s.industry, clear: "" },
    { column: "price", field: "price_minor", current: (s) => s.price },
    { column: "active", field: "is_active", current: (s) => s.active },
    {
      column: "owner",
      field: "owner_id",
      current: (s) => s.owner,
      resolve: (value, c) => (typeof value === "string" ? (c.lookups.owners?.resolve(value) ?? null) : null),
    },
  ],
  send: (existing, body) => send(existing, body),
};

const plan = (row: Record<string, string | number | boolean | null>, cleared: string[] = []) =>
  planUpdate(columns, spec, row, new Set(cleared), stored, ctx);

describe("normaliseKey and isClearToken", () => {
  it("ignores case and spacing in a key", () => {
    expect(normaliseKey("  ab   1 ")).toBe("ab 1");
  });
  it("recognises [clear] in any case and no other text", () => {
    expect(isClearToken("[clear]")).toBe(true);
    expect(isClearToken("  [CLEAR] ")).toBe(true);
    expect(isClearToken("clear")).toBe(false);
    expect(isClearToken(null)).toBe(false);
    expect(isClearToken(0)).toBe(false);
  });
});

describe("matching a row to a stored record", () => {
  const twin: Stored = { ...stored, id: "id-2", sku: "ab-1" };
  const index = indexExisting(spec, [stored, { ...stored, id: "id-3", sku: "ZZ-9" }]);

  it("matches by Record ID first", () => {
    expect(matchRow(index, "id-3", "AB-1")).toMatchObject({ kind: "found", by: "id" });
  });
  it("refuses a Record ID that no record has", () => {
    expect(matchRow(index, "id-404", null)).toEqual({ kind: "unknown-id" });
  });
  it("matches by the unique key exactly, trimming it and nothing else", () => {
    expect(matchRow(index, null, " AB-1 ")).toMatchObject({ kind: "found", by: "key", item: stored });
  });
  it("treats an unknown key as a new record", () => {
    expect(matchRow(index, null, "NEW-1")).toEqual({ kind: "new" });
    expect(matchRow(index, null, null)).toEqual({ kind: "new" });
  });
  it("refuses a key that two stored records share exactly instead of guessing", () => {
    expect(matchRow(indexExisting(spec, [stored, { ...stored, id: "id-2" }]), null, "AB-1")).toEqual({
      kind: "ambiguous",
    });
  });

  describe("under the default exact rule, as the server compares SKUs, codes and deal names", () => {
    it("finds a key that is stored exactly even when a twin in other capitals is stored too", () => {
      const both = indexExisting(spec, [stored, twin]);
      expect(matchRow(both, null, "AB-1")).toMatchObject({ kind: "found", item: stored });
      expect(matchRow(both, null, "ab-1")).toMatchObject({ kind: "found", item: twin });
    });
    it("refuses, naming the stored spelling, a key that differs from it only in capitals", () => {
      expect(matchRow(index, null, "ab-1")).toEqual({ kind: "variant", spellings: ["AB-1"] });
    });
    it("refuses a key that differs only in inner spacing, too", () => {
      const spaced = indexExisting(spec, [{ ...stored, sku: "Q4 Renewal" }]);
      expect(matchRow(spaced, null, "Q4  Renewal")).toEqual({ kind: "variant", spellings: ["Q4 Renewal"] });
    });
    it("names every stored spelling when several differ from the typed key", () => {
      const many = indexExisting(spec, [{ ...stored, sku: "AB-1" }, { ...stored, id: "id-2", sku: "Ab-1" }]);
      expect(matchRow(many, null, "aB-1")).toEqual({ kind: "variant", spellings: ["AB-1", "Ab-1"] });
    });
    it("never lets a Record ID be shadowed by a twin: the id is the record", () => {
      expect(matchRow(indexExisting(spec, [stored, twin]), "id-2", "AB-1")).toMatchObject({ kind: "found", by: "id", item: twin });
    });
  });

  describe("under keyMatch caseless, as the server compares company names", () => {
    const named: UpdateSpec<Stored> = { ...spec, keyMatch: "caseless", keyOf: (s) => s.name };
    const companies = indexExisting(named, [
      { ...stored, name: "Acme Ltd" },
      { ...stored, id: "id-2", name: "Globex  Corp" },
    ]);
    it("ignores capitals and the spaces around the name", () => {
      expect(matchRow(companies, null, "  acme LTD ")).toMatchObject({ kind: "found", by: "key", item: { id: "id-1" } });
    });
    it("keeps inner spaces, since the server does: a different run of spaces is a different name", () => {
      expect(matchRow(companies, null, "Globex Corp")).toEqual({ kind: "new" });
      expect(matchRow(companies, null, "globex  corp")).toMatchObject({ kind: "found", item: { id: "id-2" } });
    });
    it("is ambiguous when two records share the name in any capitals", () => {
      const clash = indexExisting(named, [{ ...stored, name: "Acme Ltd" }, { ...stored, id: "id-2", name: "ACME LTD" }]);
      expect(matchRow(clash, null, "acme ltd")).toEqual({ kind: "ambiguous" });
    });
  });
});

describe("what a row changes", () => {
  it("changes nothing, and asks for no request, when every cell matches", () => {
    expect(plan({ sku: "AB-1", name: "Bracket", industry: "Steel", price: 1250, active: true, owner: "Asha" })).toBeNull();
  });

  it("leaves a field alone when its cell is blank", () => {
    expect(plan({ name: null, industry: null, price: null, active: null, owner: null })).toBeNull();
  });

  it("sends only the fields that differ, keyed as the endpoint wants them", async () => {
    send.mockClear();
    const p = plan({ name: "Bracket", price: 1300, active: false, owner: "Ravi" });
    expect(p?.changed).toEqual(["Price", "Active", "Owner"]);
    await p?.run();
    expect(send).toHaveBeenCalledTimes(1);
    expect(send.mock.calls[0]?.[1]).toEqual({ price_minor: 1300, is_active: false, owner_id: "own-2" });
  });

  it("resolves a lookup label before comparing, so an unchanged owner is no change", () => {
    expect(plan({ owner: "Asha" })).toBeNull();
  });

  describe("comparing text with what the server stored", () => {
    const at = (overrides: Partial<Stored>, row: Record<string, string | number | boolean | null>) =>
      planUpdate(columns, spec, row, new Set(), { ...stored, ...overrides }, ctx);

    it("does not read stray spaces the server kept as an edit of an untouched, trimmed cell", () => {
      expect(at({ name: "Bracket  " }, { name: "Bracket" })).toBeNull();
      expect(at({ name: " Bracket" }, { name: "Bracket" })).toBeNull();
    });
    it("does not read a stored code's stray spaces as a disagreement with an immutable cell", () => {
      expect(at({ sku: "AB-1 " }, { sku: "AB-1" })).toBeNull();
    });
    it("treats every kind of line ending as one, on both sides", () => {
      expect(at({ industry: "Steel\r\nWorks" }, { industry: "Steel\nWorks" })).toBeNull();
      expect(at({ industry: "Steel\nWorks" }, { industry: "Steel\r\nWorks" })).toBeNull();
      expect(at({ industry: "Steel\rWorks" }, { industry: "Steel\nWorks" })).toBeNull();
    });
    it("still sees a real difference, including capitals and inner spaces", () => {
      expect(at({ name: "Bracket " }, { name: "bracket" })?.changed).toEqual(["Name"]);
      expect(at({ name: "Steel Bracket" }, { name: "Steel  Bracket" })?.changed).toEqual(["Name"]);
    });
    it("leaves a field's own comparison as it is, whatever the spaces", () => {
      const strict: UpdateSpec<Stored> = {
        ...spec,
        fields: [{ column: "name", current: (s) => s.name, same: (cell, was) => cell === was }],
      };
      expect(planUpdate(columns, strict, { name: "Bracket" }, new Set(), { ...stored, name: "Bracket " }, ctx)?.changed).toEqual(["Name"]);
    });
  });

  it("empties a field only when the cell says [clear]", async () => {
    send.mockClear();
    const p = plan({ industry: null }, ["industry"]);
    expect(p?.changed).toEqual(["Industry"]);
    await p?.run();
    expect(send.mock.calls[0]?.[1]).toEqual({ industry: "" });
  });

  it("does nothing to a field that is already empty", () => {
    const empty = { ...stored, industry: null };
    expect(planUpdate(columns, spec, { industry: null }, new Set(["industry"]), empty, ctx)).toBeNull();
  });

  it("refuses to clear a field that cannot be emptied", () => {
    expect(() => plan({ name: null }, ["name"])).toThrow(RowProblem);
    expect(() => plan({ name: null }, ["name"])).toThrow("Name cannot be cleared");
  });

  it("never sends an immutable field, and accepts a cell that agrees with it", () => {
    expect(plan({ sku: "AB-1" })).toBeNull();
  });

  it("refuses an immutable cell that disagrees, naming what is stored", () => {
    expect(() => plan({ sku: "AB-2" })).toThrow("SKU cannot be changed once the record exists (it is 'AB-1')");
  });

  it("refuses any value for an immutable field the list never returns, without claiming what is stored", () => {
    const unknowable: UpdateSpec<Stored> = {
      ...spec,
      fields: [{ column: "price", current: () => undefined, immutable: true }],
    };
    const attempt = () => planUpdate(columns, unknowable, { price: 0 }, new Set(), stored, ctx);
    expect(attempt).toThrow("Price cannot be changed once the record exists. Leave the cell blank.");
    expect(attempt).not.toThrow(/it is/);
    expect(planUpdate(columns, unknowable, { price: null }, new Set(), stored, ctx)).toBeNull();
  });

  it("always sends an updatable field whose stored value the list never returns", async () => {
    send.mockClear();
    const unknowable: UpdateSpec<Stored> = {
      ...spec,
      fields: [{ column: "price", field: "price_minor", current: () => undefined }],
    };
    const p = planUpdate(columns, unknowable, { price: 5 }, new Set(), stored, ctx);
    expect(p?.changed).toEqual(["Price"]);
    await p?.run();
    expect(send.mock.calls[0]?.[1]).toEqual({ price_minor: 5 });
  });

  it("refuses a filled cell, or [clear], in a column the list cannot update, instead of ignoring it", () => {
    const sealed: UpdateSpec<Stored> = { ...spec, notUpdatable: ["industry", "price"] };
    const attempt = (row: Record<string, string | number | boolean | null>, cleared: string[] = []) => () =>
      planUpdate(columns, sealed, row, new Set(cleared), stored, ctx);
    expect(attempt({ industry: "Steel" })).toThrow("Industry cannot be changed by an update: leave the cell blank.");
    expect(attempt({ industry: null }, ["industry"])).toThrow("Industry cannot be changed by an update");
    expect(attempt({ industry: "x", price: 9 })).toThrow(
      "Industry, Price cannot be changed by an update: leave the cell blank.",
    );
    // A blank cell is fine, and other columns still update.
    expect(planUpdate(columns, sealed, { industry: null, name: "Plate" }, new Set(), stored, ctx)?.changed).toEqual(["Name"]);
  });

  it("refuses to clear an immutable field", () => {
    expect(() => plan({ sku: null }, ["sku"])).toThrow("SKU cannot be cleared");
  });

  it("lets a resolve step refuse the row", () => {
    const strict: UpdateSpec<Stored> = {
      ...spec,
      fields: [
        {
          column: "owner",
          current: (s) => s.owner,
          resolve: () => {
            throw new RowProblem("Nobody is called that");
          },
        },
      ],
    };
    expect(() => planUpdate(columns, strict, { owner: "??" }, new Set(), stored, ctx)).toThrow("Nobody is called that");
  });

  it("lets the list refuse a change its own rules forbid, given what would change", () => {
    const guarded: UpdateSpec<Stored> = {
      ...spec,
      check: (_row, _existing, changed) => {
        if (changed.includes("Price")) throw new RowProblem("Price is locked");
      },
    };
    expect(() => planUpdate(columns, guarded, { price: 1 }, new Set(), stored, ctx)).toThrow("Price is locked");
    expect(planUpdate(columns, guarded, { name: "New" }, new Set(), stored, ctx)?.changed).toEqual(["Name"]);
  });

  it("uses a custom comparison, and a wire conversion, when the field has them", async () => {
    send.mockClear();
    const custom: UpdateSpec<Stored> = {
      ...spec,
      fields: [
        {
          column: "name",
          current: (s) => s.name,
          same: (cell, stored) => String(cell).toLowerCase() === String(stored).toLowerCase(),
          toWire: (value) => String(value).toUpperCase(),
        },
      ],
    };
    expect(planUpdate(columns, custom, { name: "BRACKET" }, new Set(), stored, ctx)).toBeNull();
    const p = planUpdate(columns, custom, { name: "Plate" }, new Set(), stored, ctx);
    await p?.run();
    expect(send.mock.calls[0]?.[1]).toEqual({ name: "PLATE" });
  });

  it("runs extra steps after the field update, and counts their columns as changes", async () => {
    send.mockClear();
    const order: string[] = [];
    const withExtra: UpdateSpec<Stored> = {
      ...spec,
      send: () => {
        order.push("patch");
        return Promise.resolve();
      },
      extras: () => [
        {
          changed: ["Stage"],
          run: () => {
            order.push("stage");
            return Promise.resolve();
          },
        },
      ],
    };
    const p = planUpdate(columns, withExtra, { name: "Plate" }, new Set(), stored, ctx);
    expect(p?.changed).toEqual(["Name", "Stage"]);
    await p?.run();
    expect(order).toEqual(["patch", "stage"]);
  });

  it("makes no field request when only an extra step changes something", async () => {
    send.mockClear();
    const onlyExtra: UpdateSpec<Stored> = {
      ...spec,
      extras: () => [{ changed: ["Phone"], run: () => Promise.resolve() }],
    };
    const p = planUpdate(columns, onlyExtra, { name: "Bracket" }, new Set(), stored, ctx);
    expect(p?.changed).toEqual(["Phone"]);
    await p?.run();
    expect(send).not.toHaveBeenCalled();
  });

  it("builds the diff without running anything", () => {
    const diff = buildUpdate(columns, spec, { name: "Plate" }, new Set(), stored, ctx);
    expect(diff).toEqual({ changed: ["Name"], body: { name: "Plate" } });
  });
});
