/**
 * The portfolio entity checked by the engine's own definition lint (`checkImportEntity`): every
 * column well-formed, budget and spend tied to the currency column, the owner lookup served by
 * `loadLookups`, and every sample row passing the same validation an imported row gets and
 * turning into a payload. The lookups are built the way `loadLookups` builds them, from the team
 * roster's names and ids.
 */
import { checkImportEntity, makeLookup } from "@opsgrids/client";
import type { LookupSet } from "@opsgrids/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import { portfolioEntity } from "./portfolio";

function lookupsFor(members: readonly { id: string; name: string }[]): LookupSet {
  return { owners: makeLookup(members.map((member) => ({ label: member.name, value: member.id }))) };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the portfolio entity definition", () => {
  it("passes the engine's definition check when the team has the owners the examples name", () => {
    const lookups = lookupsFor([
      { id: "p-01", name: "Meera Iyer" },
      { id: "p-02", name: "Arjun Rao" },
      { id: "p-03", name: "Neha Kulkarni" },
    ]);
    expect(checkImportEntity(portfolioEntity, lookups)).toEqual([]);
  });

  it("passes when the team has none of the names the examples use, because the sample sheet swaps in ones it has", () => {
    const lookups = lookupsFor([
      { id: "p-10", name: "Ishaan Verma" },
      { id: "p-11", name: "Neha Kulkarni" },
    ]);
    expect(checkImportEntity(portfolioEntity, lookups)).toEqual([]);
  });

  it("passes when two members share a name elsewhere on the roster", () => {
    const lookups = lookupsFor([
      { id: "p-01", name: "Meera Iyer" },
      { id: "p-02", name: "Arjun Rao" },
      { id: "p-04", name: "Sam Patel" },
      { id: "p-05", name: "Sam Patel" },
    ]);
    expect(checkImportEntity(portfolioEntity, lookups)).toEqual([]);
  });

  it("passes with the lookups loadLookups builds from every page of the roster", async () => {
    const roster = [
      { id: "p-01", name: "Meera Iyer", role: "Delivery lead", directory_id: null },
      { id: "p-02", name: "Arjun Rao", role: "Engineer", directory_id: null },
      { id: "p-03", name: "Neha Kulkarni", role: "Designer", directory_id: null },
    ];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string) => {
        const page = Number(new URL(input, "http://gateway.test").searchParams.get("page") ?? "1");
        const items = roster.slice((page - 1) * 2, page * 2);
        return new Response(JSON.stringify({ items, total: roster.length, page, size: 2, pages: 2 }), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      }),
    );
    const lookups = await portfolioEntity.loadLookups?.(new AbortController().signal);
    expect(lookups?.owners?.labels).toEqual(["Meera Iyer", "Arjun Rao", "Neha Kulkarni"]);
    expect(checkImportEntity(portfolioEntity, lookups ?? {})).toEqual([]);
  });
});

describe("the portfolio update spec", () => {
  const update = portfolioEntity.update;

  it("is there, and is what the engine's definition check lints", () => {
    expect(update).toBeDefined();
    const lookups = lookupsFor([
      { id: "p-01", name: "Meera Iyer" },
      { id: "p-02", name: "Arjun Rao" },
    ]);
    expect(checkImportEntity(portfolioEntity, lookups)).toEqual([]);
  });

  it("covers every importable column: each is an update field, so nothing needs a notUpdatable or extraColumns entry", () => {
    const covered = new Set([...(update?.fields.map((f) => f.column) ?? []), ...(update?.extraColumns ?? [])]);
    const importable = portfolioEntity.columns.filter((c) => c.readOnly !== true && c.importOnly !== true);
    expect(importable.filter((c) => !covered.has(c.key)).map((c) => c.key)).toEqual([]);
    expect(update?.notUpdatable ?? []).toEqual([]);
    expect(update?.extraColumns ?? []).toEqual([]);
  });

  it("gives the owner column an update field with a resolve, so a name is never compared or sent as the value", () => {
    expect(update?.fields.find((f) => f.column === "owner")?.resolve).toBeDefined();
    expect(portfolioEntity.columns.find((c) => c.key === "owner")?.lookupHintUpdate).toBeTruthy();
  });
});
