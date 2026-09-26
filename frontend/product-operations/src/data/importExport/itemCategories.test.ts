import { afterEach, describe, expect, it, vi } from "vitest";

import { RowProblem, makeLookup } from "@opsgrids/client";
import type { ImportContext, LookupSet, ParsedRow } from "@opsgrids/client";

import type { ItemCategoryOut } from "@/api/types";
import { pageAt, pageOf, stubGateway } from "./entityTestKit";
import { itemCategoryImport } from "./itemCategories";

afterEach(() => {
  vi.unstubAllGlobals();
});

/** What `loadLookups` builds for an organisation with these two categories: the codes to ids, and
 *  the codes exactly as spelled. */
const LOOKUPS: LookupSet = {
  parents: makeLookup([
    { label: "METALS", value: "cat-metals" },
    { label: "STEEL", value: "cat-steel" },
  ]),
  codes: makeLookup([
    { label: "METALS", value: "METALS" },
    { label: "STEEL", value: "STEEL" },
  ]),
};

const CONTEXT: ImportContext = { lookups: LOOKUPS, signal: new AbortController().signal };

function loadLookups(signal: AbortSignal): Promise<LookupSet> {
  if (itemCategoryImport.loadLookups === undefined) throw new Error("item categories have no lookups");
  return itemCategoryImport.loadLookups(signal);
}

function exportSource() {
  if (itemCategoryImport.export === undefined) throw new Error("item categories have no export");
  return itemCategoryImport.export;
}

function category(code: string, id: string, parentId: string | null = null): ItemCategoryOut {
  return { id, code, name: code.charAt(0) + code.slice(1).toLowerCase(), parent_id: parentId };
}

describe("item category columns", () => {
  it("names them in plain business English", () => {
    expect(itemCategoryImport.columns.map((c) => c.header)).toEqual([
      "Category code",
      "Category name",
      "Parent category code",
      "Record ID",
    ]);
  });

  it("requires the code and the name, and keeps the id read-only", () => {
    const required = itemCategoryImport.columns.filter((c) => c.required === true).map((c) => c.key);
    const readOnly = itemCategoryImport.columns.filter((c) => c.readOnly === true).map((c) => c.key);
    expect(required).toEqual(["code", "name"]);
    expect(readOnly).toEqual(["id"]);
  });

  it("tells the importer to import parents first and run the file again", () => {
    const notes = (itemCategoryImport.notes ?? []).join(" ");
    expect(notes).toMatch(/parent category must already exist/i);
    expect(notes).toMatch(/import the top-level categories first, then run the file again/i);
  });

  it("carries the fix for an unknown parent on the parent column, where the engine adds it to the error", () => {
    const parent = itemCategoryImport.columns.find((c) => c.key === "parent");
    expect(parent?.lookup).toBe("parents");
    expect(parent?.lookupHint).toBe(
      "Import the parent first, then run this file again: rows already imported are skipped.",
    );
  });

  it("names its id column as the record id, so a row that came out of an export is not added again", () => {
    expect(itemCategoryImport.recordIdKey).toBe("id");
    expect(itemCategoryImport.columns.find((c) => c.key === itemCategoryImport.recordIdKey)?.readOnly).toBe(true);
  });
});

describe("item category sample rows", () => {
  // Whether they pass the column rules and turn into a payload is `checkEntities.test.ts`'s job.
  it("has two or three top-level ones, none depending on another row", () => {
    expect(itemCategoryImport.sampleRows.length).toBeGreaterThanOrEqual(2);
    expect(itemCategoryImport.sampleRows.length).toBeLessThanOrEqual(3);
    for (const row of itemCategoryImport.sampleRows) expect(row.parent ?? null).toBeNull();
  });
});

describe("item category toPayload", () => {
  const ROW: ParsedRow = { code: "STAINLESS", name: "Stainless steel", parent: null };

  it("creates a top-level category when the parent cell is blank", () => {
    expect(itemCategoryImport.toPayload(ROW, CONTEXT)).toStrictEqual({
      code: "STAINLESS",
      name: "Stainless steel",
    });
  });

  it("turns a parent code into the id the API wants, whatever its capitals", () => {
    expect(itemCategoryImport.toPayload({ ...ROW, parent: "steel" }, CONTEXT)).toStrictEqual({
      code: "STAINLESS",
      name: "Stainless steel",
      parent_id: "cat-steel",
    });
  });

  // The engine refuses a parent the lookup cannot resolve, with the column's `lookupHint` (which
  // says to import the parent first), before `toPayload` runs. `toPayload` still fails the row
  // rather than send a guess.
  it("fails the row for a parent that is not there yet", () => {
    const attempt = () => itemCategoryImport.toPayload({ ...ROW, parent: "ALLOYS" }, CONTEXT);
    expect(attempt).toThrow(RowProblem);
    expect(attempt).toThrow('Parent category code "ALLOYS" does not match exactly one entry on the Lists sheet.');
  });

  it("fails the row for a parent code two categories share rather than picking one", () => {
    const lookups: LookupSet = {
      parents: makeLookup([
        { label: "Steel", value: "cat-a" },
        { label: "STEEL", value: "cat-b" },
      ]),
    };
    const attempt = () =>
      itemCategoryImport.toPayload({ ...ROW, parent: "steel" }, { ...CONTEXT, lookups });
    expect(attempt).toThrow(RowProblem);
  });

  it("accepts a code no existing category resembles", () => {
    expect(itemCategoryImport.toPayload({ ...ROW, code: "ALLOYS" }, CONTEXT)).toMatchObject({ code: "ALLOYS" });
  });

  it("sends a code spelled exactly like an existing one, so the server's 409 makes it a skipped row", () => {
    expect(itemCategoryImport.toPayload({ ...ROW, code: "STEEL" }, CONTEXT)).toMatchObject({ code: "STEEL" });
  });

  it.each(["steel", "Steel", "sTeEl"])(
    "refuses %j, which differs from an existing code only in capitals, and says which one",
    (code) => {
      const attempt = () => itemCategoryImport.toPayload({ ...ROW, code }, CONTEXT);
      expect(attempt).toThrow(RowProblem);
      expect(attempt).toThrow(
        "An item category with the code 'STEEL' already exists; codes that differ only in capitals are not accepted.",
      );
    },
  );

  it("still lets the exact spelling of either of two existing case variants through", () => {
    const lookups: LookupSet = {
      parents: makeLookup([
        { label: "Steel", value: "cat-a" },
        { label: "STEEL", value: "cat-b" },
      ]),
      codes: makeLookup([
        { label: "Steel", value: "Steel" },
        { label: "STEEL", value: "STEEL" },
      ]),
    };
    const context = { ...CONTEXT, lookups };
    expect(() => itemCategoryImport.toPayload({ ...ROW, code: "Steel" }, context)).not.toThrow();
    expect(() => itemCategoryImport.toPayload({ ...ROW, code: "STEEL" }, context)).not.toThrow();
    expect(() => itemCategoryImport.toPayload({ ...ROW, code: "steel" }, context)).toThrow(
      "More than one item category already has a code that differs from 'steel' only in capitals, so it is not accepted.",
    );
  });

  it("refuses a missing required cell", () => {
    expect(() => itemCategoryImport.toPayload({ ...ROW, code: null }, CONTEXT)).toThrow(
      /Category code is required/,
    );
  });
});

describe("item category loadLookups", () => {
  it("reads every page and offers each existing category's code as a parent", async () => {
    const recorded = stubGateway(({ url }) => {
      const pages = [
        [category("METALS", "cat-metals"), category("STEEL", "cat-steel", "cat-metals")],
        [category("POLYMERS", "cat-polymers")],
      ];
      return { body: pageAt(pages, Number(url.searchParams.get("page"))) };
    });

    const lookups = await loadLookups(new AbortController().signal);

    expect(lookups.parents?.labels).toEqual(["METALS", "STEEL", "POLYMERS"]);
    expect(lookups.parents?.resolve("polymers")).toBe("cat-polymers");
    // The same categories, each code mapped to itself, for telling an exact spelling from a variant.
    expect(lookups.codes?.labelOf("STEEL")).toBe("STEEL");
    expect(lookups.codes?.labelOf("steel")).toBeUndefined();
    expect(lookups.codes?.resolve("steel")).toBe("STEEL");
    expect(recorded).toHaveLength(2);
    for (const request of recorded) expect(request.url.searchParams.get("size")).toBe("200");
  });

  it("keeps a category that two pages both served as one, not ambiguous, and loses none", async () => {
    // Ordered by a name that is not unique, STEEL sits on both sides of the page boundary.
    const served = [
      [category("METALS", "cat-metals"), category("STEEL", "cat-steel")],
      [category("STEEL", "cat-steel"), category("POLYMERS", "cat-polymers")],
    ];
    stubGateway(({ url }) => {
      const page = Number(url.searchParams.get("page"));
      return { body: { items: served[page - 1] ?? [], total: 3, page, size: 200, pages: 2 } };
    });

    const lookups = await loadLookups(new AbortController().signal);

    expect(lookups.parents?.labels).toEqual(["METALS", "STEEL", "POLYMERS"]);
    expect(lookups.parents?.isAmbiguous("STEEL")).toBe(false);
  });

  it("refuses a list that stays short of the server's total, rather than offering parents missing a real category", async () => {
    stubGateway(({ url }) => {
      const page = Number(url.searchParams.get("page"));
      return { body: { items: [category("STEEL", "cat-steel")], total: 2, page, size: 200, pages: 1 } };
    });
    await expect(loadLookups(new AbortController().signal)).rejects.toThrow("changed while it was being read");
  });

  it("provides every lookup the columns name", async () => {
    stubGateway(() => ({ body: pageOf([]) }));
    const lookups = await loadLookups(new AbortController().signal);
    for (const column of itemCategoryImport.columns) {
      if (column.lookup !== undefined) expect(lookups[column.lookup]).toBeDefined();
    }
  });
});

describe("item category create", () => {
  it("posts the payload to the categories endpoint and reports the new id", async () => {
    const recorded = stubGateway(() => ({ body: { id: "cat-9" }, status: 201 }));
    const payload = itemCategoryImport.toPayload(
      { code: "STAINLESS", name: "Stainless steel", parent: "STEEL" },
      CONTEXT,
    );

    const result = await itemCategoryImport.create(payload, CONTEXT);

    expect(recorded[0]?.url.pathname).toBe("/api/v1/operations/inventory/categories");
    expect(recorded[0]?.init.method).toBe("POST");
    expect(JSON.parse(String(recorded[0]?.init.body))).toEqual({
      code: "STAINLESS",
      name: "Stainless steel",
      parent_id: "cat-steel",
    });
    expect(result).toEqual({ id: "cat-9" });
  });

  it("lets a duplicate code refusal reach the engine so the row is reported as skipped", async () => {
    stubGateway(() => ({
      body: {
        title: "Conflict",
        status: 409,
        detail: "A category coded 'STEEL' already exists",
        errors: [{ field: "code", code: "duplicate", message: "A category coded 'STEEL' already exists" }],
      },
      status: 409,
    }));
    await expect(
      itemCategoryImport.create({ code: "STEEL", name: "Steel" }, CONTEXT),
    ).rejects.toMatchObject({ status: 409 });
  });
});

describe("item category export", () => {
  it("pages the whole list, passing the abort signal on", async () => {
    const controller = new AbortController();
    const recorded = stubGateway(() => ({ body: pageOf([category("METALS", "cat-metals")], 1, 1) }));

    const result = await exportSource().fetchPage({
      page: 1,
      size: 200,
      signal: controller.signal,
      filters: undefined,
    });

    expect(recorded[0]?.url.pathname).toBe("/api/v1/operations/inventory/categories");
    expect(recorded[0]?.url.searchParams.get("size")).toBe("200");
    expect(recorded[0]?.init.signal).toBe(controller.signal);
    expect(result.items).toHaveLength(1);
  });

  it("writes the parent as its code, so the sheet reads as a tree", () => {
    const row = exportSource().toRow(category("STEEL", "cat-steel", "cat-metals"), LOOKUPS);
    expect(row).toStrictEqual({ code: "STEEL", name: "Steel", parent: "METALS", id: "cat-steel" });
    expect(itemCategoryImport.columns.map((c) => c.key).filter((key) => !(key in row))).toEqual([]);
  });

  it("leaves the parent blank for a top-level category or one whose parent cannot be named", () => {
    expect(exportSource().toRow(category("METALS", "cat-metals"), LOOKUPS).parent).toBeNull();
    expect(exportSource().toRow(category("ORPHAN", "cat-x", "cat-gone"), LOOKUPS).parent).toBeNull();
  });
});
