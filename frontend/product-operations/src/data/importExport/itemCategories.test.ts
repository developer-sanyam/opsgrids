import { afterEach, describe, expect, it, vi } from "vitest";

import { RowProblem, makeLookup } from "@opsgrids/client";
import type { ImportContext, LookupSet, ParsedRow } from "@opsgrids/client";

import type { ItemCategoryOut } from "@/api/types";
import { pageOf, stubGateway } from "./entityTestKit";
import { itemCategoryImport } from "./itemCategories";

afterEach(() => {
  vi.unstubAllGlobals();
});

const LOOKUPS: LookupSet = {
  parents: makeLookup([
    { label: "METALS", value: "cat-metals" },
    { label: "STEEL", value: "cat-steel" },
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
      "ID",
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

  it("refuses a parent that is not there yet, and says what to do", () => {
    const attempt = () => itemCategoryImport.toPayload({ ...ROW, parent: "ALLOYS" }, CONTEXT);
    expect(attempt).toThrow(RowProblem);
    expect(attempt).toThrow(/parent category "ALLOYS" does not exist yet\. Import the parent first, then run this file again/);
  });

  it("refuses a parent code two categories share rather than picking one", () => {
    const lookups: LookupSet = {
      parents: makeLookup([
        { label: "Steel", value: "cat-a" },
        { label: "STEEL", value: "cat-b" },
      ]),
    };
    const attempt = () =>
      itemCategoryImport.toPayload({ ...ROW, parent: "steel" }, { ...CONTEXT, lookups });
    expect(attempt).toThrow(RowProblem);
    expect(attempt).toThrow(/More than one category has a code matching "steel"/);
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
      const page = Number(url.searchParams.get("page"));
      return {
        body: pageOf(
          page === 1
            ? [category("METALS", "cat-metals"), category("STEEL", "cat-steel", "cat-metals")]
            : [category("POLYMERS", "cat-polymers")],
          page,
          2,
        ),
      };
    });

    const lookups = await loadLookups(new AbortController().signal);

    expect(lookups.parents?.labels).toEqual(["METALS", "STEEL", "POLYMERS"]);
    expect(lookups.parents?.resolve("polymers")).toBe("cat-polymers");
    expect(recorded).toHaveLength(2);
    for (const request of recorded) expect(request.url.searchParams.get("size")).toBe("200");
  });

  it("keeps a category that two pages both served as one, not ambiguous", async () => {
    stubGateway(({ url }) => {
      const page = Number(url.searchParams.get("page"));
      return { body: pageOf([category("STEEL", "cat-steel")], page, 2) };
    });

    const lookups = await loadLookups(new AbortController().signal);

    expect(lookups.parents?.labels).toEqual(["STEEL"]);
    expect(lookups.parents?.isAmbiguous("STEEL")).toBe(false);
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
