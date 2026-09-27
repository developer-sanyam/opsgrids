/**
 * Updating existing item categories from a sheet (ADR-0022 addendum). A category is renamed or
 * re-parented; its code never changes and its name is never emptied. Whether a re-parenting would
 * make the tree circular is the server's answer, given the tree as it stands when the row is sent,
 * so these tests hold the spec to the body it sends and to surfacing that answer per row.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, RowProblem, makeLookup } from "@opsgrids/client";
import type { ImportContext, LookupSet, ParsedRow } from "@opsgrids/client";

import { itemCategoryOut, pageAt, stubGateway } from "./entityTestKit";
import { itemCategoryImport } from "./itemCategories";
import {
  containsNull,
  expectPlan,
  matchAmong,
  planFor,
  requestsOf,
  soleRequest,
  stepsAndRequests,
  updateSpec,
} from "./updateTestKit";

afterEach(() => {
  vi.unstubAllGlobals();
});

const METALS = itemCategoryOut("METALS", "cat-metals");
const STEEL = { ...itemCategoryOut("STEEL", "cat-steel", "cat-metals"), name: "Steel" };
const ALUMINIUM = itemCategoryOut("ALUMINIUM", "cat-alu", "cat-metals");

const LOOKUPS: LookupSet = {
  parents: makeLookup([
    { label: "METALS", value: "cat-metals" },
    { label: "STEEL", value: "cat-steel" },
    { label: "ALUMINIUM", value: "cat-alu" },
    { label: "POLYMERS", value: "cat-poly" },
  ]),
  codes: makeLookup([
    { label: "METALS", value: "METALS" },
    { label: "STEEL", value: "STEEL" },
    { label: "ALUMINIUM", value: "ALUMINIUM" },
    { label: "POLYMERS", value: "POLYMERS" },
  ]),
};

const ctx: ImportContext = { lookups: LOOKUPS, signal: new AbortController().signal };

const PATH = "/api/v1/operations/inventory/categories/cat-steel";

const SAME: ParsedRow = { code: "STEEL", name: "Steel", parent: "METALS" };
const BLANK: ParsedRow = { code: null, name: null, parent: null };

const plan = (row: ParsedRow, cleared: readonly string[] = [], existing = STEEL) =>
  planFor(itemCategoryImport, existing, row, ctx, cleared);

describe("a row that changes nothing", () => {
  it("makes no request when every cell already matches the category", async () => {
    expect(plan(SAME)).toBeNull();
    expect(await requestsOf(plan(SAME))).toEqual([]);
  });

  it("recognises the parent in other capitals as the same", () => {
    expect(plan({ code: "STEEL", name: "Steel", parent: "metals" })).toBeNull();
  });

  describe("when two categories carry the parent's code in other capitals", () => {
    const twins: ImportContext = {
      lookups: {
        ...LOOKUPS,
        parents: makeLookup([
          { label: "METALS", value: "cat-metals" },
          { label: "Metals", value: "cat-metals-2" },
          { label: "STEEL", value: "cat-steel" },
          { label: "POLYMERS", value: "cat-poly" },
        ]),
      },
      signal: new AbortController().signal,
    };
    const planIn = (existing: typeof STEEL, row: ParsedRow, cleared: readonly string[] = []) =>
      planFor(itemCategoryImport, existing, row, twins, cleared);
    const underMetals = { ...STEEL, parent_id: "cat-metals" };
    const underMetalsAgain = { ...STEEL, parent_id: "cat-metals-2" };

    it("is no change when the cell repeats the code of the category's own parent, whichever twin that is", () => {
      expect(planIn(underMetals, { ...BLANK, parent: "METALS" })).toBeNull();
      expect(planIn(underMetalsAgain, { ...BLANK, parent: "Metals" })).toBeNull();
    });

    it("leaves the other cells of such a row to change what they change", async () => {
      const p = planIn(underMetals, { ...BLANK, name: "Steel sections", parent: "METALS" });
      expect(p?.changed).toEqual(["Category name"]);
      expect((await soleRequest(p)).body).toEqual({ name: "Steel sections" });
    });

    it("refuses the other twin and a spelling that fits both, since the row cannot say which parent it means", () => {
      expect(() => planIn(underMetals, { ...BLANK, parent: "Metals" })).toThrow(
        "'Metals' matches more than one of the accepted values for Parent category code, so it cannot be chosen.",
      );
      expect(() => planIn(underMetals, { ...BLANK, parent: "metals" })).toThrow(RowProblem);
    });

    it("still re-parents to a code only one category carries, and to the top level with [clear]", async () => {
      expect((await soleRequest(planIn(underMetals, { ...BLANK, parent: "polymers" }))).body).toEqual({
        parent_id: "cat-poly",
      });
      expect((await soleRequest(planIn(underMetals, { ...BLANK }, ["parent"]))).body).toEqual({ parent_id: null });
    });
  });

  it("holds a row found by its Record ID to the code's exact spelling: other capitals is an attempt to change it", () => {
    // A row found by typing its code is handed the stored spelling by the engine (see updateFlow.test.tsx).
    expect(() => plan({ ...BLANK, code: "steel" })).toThrow(
      "Category code cannot be changed once the record exists (it is 'STEEL')",
    );
  });

  it("leaves every field alone when every cell is blank", () => {
    expect(plan(BLANK)).toBeNull();
  });

  it("leaves a top-level category alone when the parent cell is blank", () => {
    expect(plan({ ...BLANK, name: "Metals" }, [], { ...METALS, name: "Metals" })).toBeNull();
  });
});

describe("a row that changes a field", () => {
  it("renames the category with only the name in the body", async () => {
    const p = plan({ ...SAME, name: "Structural steel" });
    expect(p?.changed).toEqual(["Category name"]);
    const request = await soleRequest(p);
    expect(request).toEqual({ method: "PATCH", path: PATH, body: { name: "Structural steel" } });
    expect(containsNull(request.body)).toBe(false);
  });

  it("moves the category under another parent, sending the parent's id under the API's key", async () => {
    const p = plan({ ...SAME, parent: "aluminium" });
    expect(p?.changed).toEqual(["Parent category code"]);
    expect(await soleRequest(p)).toEqual({ method: "PATCH", path: PATH, body: { parent_id: "cat-alu" } });
  });

  it("is one step and one request however many fields change, so a rate-limited request is retried on its own", async () => {
    const p = plan({ ...BLANK, name: "Structural steel", parent: "POLYMERS" });
    expect(p?.changed).toHaveLength(2);
    expect(await stepsAndRequests(p)).toEqual({ steps: 1, requests: 1 });
  });

  it("gives a top-level category its first parent", async () => {
    const p = plan({ ...BLANK, parent: "METALS" }, [], { ...STEEL, parent_id: null });
    expect((await soleRequest(p)).body).toEqual({ parent_id: "cat-metals" });
  });

  it("renames and moves in one request, and only the columns the sheet has", async () => {
    const p = plan({ ...BLANK, name: "Structural steel", parent: "POLYMERS" });
    expect((await soleRequest(p)).body).toEqual({ name: "Structural steel", parent_id: "cat-poly" });
  });
});

describe("[clear]", () => {
  it("moves the category to the top level with null, the one place this endpoint takes one", async () => {
    const p = plan({ ...BLANK }, ["parent"]);
    expect(p?.changed).toEqual(["Parent category code"]);
    expect(await soleRequest(p)).toEqual({ method: "PATCH", path: PATH, body: { parent_id: null } });
  });

  it("does nothing to a category that is already at the top level", () => {
    expect(plan({ ...BLANK }, ["parent"], { ...STEEL, parent_id: null })).toBeNull();
  });

  it("refuses to clear the name, which the endpoint answers with a 422", () => {
    expect(() => plan({ ...BLANK }, ["name"])).toThrow(RowProblem);
    expect(() => plan({ ...BLANK }, ["name"])).toThrow("Category name cannot be cleared");
  });

  it("refuses to clear the code", () => {
    expect(() => plan({ ...BLANK }, ["code"])).toThrow(
      "Category code cannot be cleared: it cannot be changed once the record exists",
    );
  });
});

describe("what can never change", () => {
  it("refuses a code that names another category, saying what is stored, and sends nothing", () => {
    const attempt = () => plan({ ...SAME, code: "ALUMINIUM", name: "Renamed" });
    expect(attempt).toThrow(RowProblem);
    expect(attempt).toThrow("Category code cannot be changed once the record exists (it is 'STEEL')");
  });

  it("never puts the code in a body, whatever the row says", async () => {
    const p = plan({ ...SAME, name: "Structural steel" });
    expect(Object.keys((await soleRequest(p)).body as object)).toEqual(["name"]);
  });
});

describe("the parent", () => {
  it("refuses a parent code that does not exist, telling the user how to fix it", () => {
    const attempt = () => plan({ ...BLANK, parent: "NOPE" });
    expect(attempt).toThrow(RowProblem);
    expect(attempt).toThrow(
      "'NOPE' is not one of the accepted values for Parent category code (see the Lists sheet). " +
        "The parent must already exist, so add it in an earlier run. " +
        "A blank cell keeps the category's parent, and [clear] moves it to the top level.",
    );
  });

  it("refuses a parent code that two categories share, instead of picking one", () => {
    const twins: LookupSet = {
      ...LOOKUPS,
      parents: makeLookup([
        { label: "METALS", value: "cat-metals" },
        { label: "Metals", value: "cat-metals-2" },
      ]),
    };
    expect(() => planFor(itemCategoryImport, STEEL, { ...BLANK, parent: "metals" }, { ...ctx, lookups: twins })).toThrow(
      RowProblem,
    );
  });

  it("never sends a guess when the categories were not loaded", () => {
    expect(() => planFor(itemCategoryImport, STEEL, { ...BLANK, parent: "METALS" }, { ...ctx, lookups: {} })).toThrow(
      RowProblem,
    );
  });

  it("leaves a move under itself, and a move that closes a loop, to the server and shows its sentence as the row's failure", async () => {
    const own = expectPlan(plan({ ...BLANK, parent: "STEEL" }));
    expect(await soleRequest(own)).toEqual({ method: "PATCH", path: PATH, body: { parent_id: "cat-steel" } });

    const sentence = "That would make the category tree circular";
    stubGateway(() => ({
      status: 422,
      body: { type: "https://errors.opsgrids.dev/validation", title: "Validation failed", status: 422, detail: sentence },
    }));
    const loop = expectPlan(planFor(itemCategoryImport, METALS, { ...BLANK, parent: "STEEL" }, ctx));
    await expect(loop.run()).rejects.toBeInstanceOf(ApiError);
    await expect(loop.run()).rejects.toMatchObject({ status: 422, message: sentence });
  });

  it("sends the rows of a file in the order the plans are run, since each is checked against the tree as it then stands", async () => {
    const first = expectPlan(plan({ ...BLANK, parent: "aluminium" }));
    const second = expectPlan(planFor(itemCategoryImport, ALUMINIUM, { ...BLANK, parent: "POLYMERS" }, ctx));
    const recorded = stubGateway(() => ({ body: {} }));

    await first.run();
    await second.run();

    expect(recorded.map((request) => [request.url.pathname, request.init.body])).toEqual([
      [PATH, JSON.stringify({ parent_id: "cat-alu" })],
      ["/api/v1/operations/inventory/categories/cat-alu", JSON.stringify({ parent_id: "cat-poly" })],
    ]);
  });
});

describe("finding the category a row names", () => {
  it("matches by Record ID first, and by the code, surrounding spaces ignored", () => {
    expect(matchAmong(itemCategoryImport, [METALS, STEEL], "cat-steel", null)).toEqual({
      kind: "found",
      item: STEEL,
      by: "id",
    });
    expect(matchAmong(itemCategoryImport, [METALS, STEEL], null, " STEEL ")).toEqual({
      kind: "found",
      item: STEEL,
      by: "key",
    });
  });

  it("names the category a row updates by its code and name", () => {
    expect(updateSpec(itemCategoryImport).recordLabel?.(STEEL)).toBe("STEEL · Steel");
    expect(updateSpec(itemCategoryImport).recordLabel?.(METALS)).toBe("METALS · METALS");
  });

  it("treats a code nobody has as a new category, and a Record ID nobody has as an error", () => {
    expect(matchAmong(itemCategoryImport, [METALS, STEEL], null, "POLYMERS")).toEqual({ kind: "new" });
    expect(matchAmong(itemCategoryImport, [METALS, STEEL], "cat-404", "STEEL")).toEqual({ kind: "unknown-id" });
  });

  it("refuses a code that two categories share, instead of picking one", () => {
    const twin = itemCategoryOut("STEEL", "cat-steel-2");
    expect(matchAmong(itemCategoryImport, [METALS, STEEL, twin], null, "STEEL")).toEqual({ kind: "ambiguous" });
  });
});

describe("item category loadExisting", () => {
  const spec = updateSpec(itemCategoryImport);

  it("reads every page of the whole tree and keeps each category once", async () => {
    const served = [[METALS, STEEL], [STEEL, ALUMINIUM]];
    const recorded = stubGateway(({ url }) => {
      const page = Number(url.searchParams.get("page"));
      return { body: { items: served[page - 1] ?? [], total: 3, page, size: 200, pages: 2 } };
    });

    const items = await spec.loadExisting(ctx);

    expect(items.map((item) => item.code)).toEqual(["METALS", "STEEL", "ALUMINIUM"]);
    expect(recorded.map((request) => request.url.pathname)).toEqual([
      "/api/v1/operations/inventory/categories",
      "/api/v1/operations/inventory/categories",
    ]);
    expect(recorded.map((request) => Object.fromEntries(request.url.searchParams))).toEqual([
      { page: "1", size: "200" },
      { page: "2", size: "200" },
    ]);
  });

  it("reads the tree afresh on every call", async () => {
    let parent: string | null = "cat-metals";
    stubGateway(() => ({ body: pageAt([[{ ...STEEL, parent_id: parent }]], 1) }));

    const before = await spec.loadExisting(ctx);
    parent = null;
    const after = await spec.loadExisting(ctx);

    expect([before[0]?.parent_id, after[0]?.parent_id]).toEqual(["cat-metals", null]);
  });
});

describe("item category update notes", () => {
  it("says what cannot change, what [clear] does, and that the order of the rows decides a re-parenting", () => {
    const notes = (itemCategoryImport.notes ?? []).filter((note) => note.startsWith("Updating:")).join(" ");
    expect(notes).toMatch(/Category code cannot be changed/);
    expect(notes).toMatch(/\[clear\] moves it to the top level. Category name cannot be emptied/);
    expect(notes).toMatch(/checked as its row is sent, in file order.*make the tree circular is refused for that row/);
  });

  it("says that a code must be spelled as stored, and that other capitals need that spelling or the Record ID", () => {
    const notes = (itemCategoryImport.notes ?? []).join(" ");
    expect(notes).toMatch(/by its code spelled exactly as it is stored/);
    expect(notes).toMatch(/differs only in capitals is refused, naming the stored spelling: use that spelling, or the Record ID/);
    expect(notes).not.toMatch(/still finds its category/);
  });

  it("says that a code that only differs in capitals from an earlier row of the file is refused", () => {
    expect((itemCategoryImport.notes ?? []).join(" ")).toMatch(
      /so is a code that only differs in capitals from one an earlier row of the same file gives/,
    );
  });

  it("says in the parent column's help how to move a category to the top level", () => {
    expect(itemCategoryImport.columns.find((column) => column.key === "parent")?.help).toMatch(/use \[clear\]/);
  });
});
