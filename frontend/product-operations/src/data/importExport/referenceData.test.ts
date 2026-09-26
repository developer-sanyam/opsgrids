import { afterEach, describe, expect, it, vi } from "vitest";

import type { ItemCategoryOut, UomOut, WarehouseOut } from "@/api/types";
import { itemCategoryOut, pageAt, requestsTo, stubGateway, uomOut, warehouseOut } from "./entityTestKit";
import { loadItemCategoryCodes, loadUnits, loadWarehouseNames } from "./referenceData";

afterEach(() => {
  vi.unstubAllGlobals();
});

const WAREHOUSES = "/api/v1/operations/warehouses";
const UNITS = "/api/v1/operations/inventory/uoms";
const CATEGORIES = "/api/v1/operations/inventory/categories";

const signal = () => new AbortController().signal;

describe("the reference lists", () => {
  it("read every page, each with the size the gateway allows", async () => {
    const pages = [[warehouseOut("Main Warehouse", "WH-MAIN")], [warehouseOut("Cold Store", "WH-COLD")]];
    const recorded = stubGateway(({ url }) => ({ body: pageAt(pages, Number(url.searchParams.get("page"))) }));

    const lookup = await loadWarehouseNames(signal());

    expect(lookup.labels).toEqual(["Main Warehouse", "Cold Store"]);
    expect(lookup.resolve("cold store")).toBe("Cold Store");
    const asked = requestsTo(recorded, "GET", WAREHOUSES);
    expect(asked.map((request) => request.url.searchParams.get("page"))).toEqual(["1", "2"]);
    for (const request of asked) expect(request.url.searchParams.get("size")).toBe("200");
  });

  it("keep a record once when the server served it on two pages, and lose none", async () => {
    const metals = itemCategoryOut("METALS", "cat-metals");
    const steel = itemCategoryOut("STEEL", "cat-steel");
    const aluminium = itemCategoryOut("ALUMINIUM", "cat-alu");
    // Ordering by a name that is not unique: STEEL sits on both sides of the page boundary.
    const served: ItemCategoryOut[][] = [[metals, steel], [steel, aluminium]];
    const recorded = stubGateway(({ url }) => {
      const page = Number(url.searchParams.get("page"));
      return { body: { items: served[page - 1] ?? [], total: 3, page, size: 200, pages: 2 } };
    });

    const lookup = await loadItemCategoryCodes(signal());

    expect(lookup.labels).toEqual(["METALS", "STEEL", "ALUMINIUM"]);
    expect(lookup.isAmbiguous("steel")).toBe(false);
    expect(requestsTo(recorded, "GET", CATEGORIES)).toHaveLength(2);
  });

  it("read a list again when a repeat made the first walk short, and use the complete second one", async () => {
    const metals = itemCategoryOut("METALS", "cat-metals");
    const steel = itemCategoryOut("STEEL", "cat-steel");
    const aluminium = itemCategoryOut("ALUMINIUM", "cat-alu");
    let walks = 0;
    const recorded = stubGateway(({ url }) => {
      const page = Number(url.searchParams.get("page"));
      if (page === 1) walks += 1;
      // The first walk repeats STEEL and skips ALUMINIUM; the second is served properly.
      const served: ItemCategoryOut[][] = walks === 1 ? [[metals, steel], [steel]] : [[metals, steel], [aluminium]];
      return { body: { items: served[page - 1] ?? [], total: 3, page, size: 200, pages: 2 } };
    });

    const lookup = await loadItemCategoryCodes(signal());

    expect(lookup.labels).toEqual(["METALS", "STEEL", "ALUMINIUM"]);
    expect(lookup.resolve("steel")).toBe("cat-steel");
    expect(requestsTo(recorded, "GET", CATEGORIES)).toHaveLength(4);
  });

  it("refuse a list that is still short of the server's total after a second read, rather than hand it over partial", async () => {
    const metals = itemCategoryOut("METALS", "cat-metals");
    const steel = itemCategoryOut("STEEL", "cat-steel");
    stubGateway(({ url }) => {
      const page = Number(url.searchParams.get("page"));
      const served: ItemCategoryOut[][] = [[metals, steel], [steel]];
      return { body: { items: served[page - 1] ?? [], total: 3, page, size: 200, pages: 2 } };
    });

    await expect(loadItemCategoryCodes(signal())).rejects.toThrow("changed while it was being read");
  });

  it("map an item category's code to its id", async () => {
    stubGateway(() => ({ body: pageAt([[itemCategoryOut("METALS", "cat-metals")]], 1) }));
    const lookup = await loadItemCategoryCodes(signal());
    expect(lookup.resolve("metals")).toBe("cat-metals");
    expect(lookup.labelOf("cat-metals")).toBe("METALS");
  });

  it("list units by their code, or by the spelling a custom unit was created with, and take the Tally spellings too", async () => {
    const units: UomOut[] = [uomOut("pcs", "Piece", true), uomOut("kg", "Kilogram", true), uomOut("mt", "MT", false)];
    stubGateway(() => ({ body: pageAt([units], 1) }));

    const lookup = await loadUnits(signal());

    expect(lookup.labels).toEqual(["pcs", "kg", "MT"]);
    expect(lookup.resolve("Nos")).toBe("pcs");
    expect(lookup.resolve("mt")).toBe("MT");
  });

  it("pass the abort signal on to every request, and stop before asking when it has fired", async () => {
    const controller = new AbortController();
    const recorded = stubGateway(() => ({ body: pageAt([[] as WarehouseOut[]], 1) }));
    await loadWarehouseNames(controller.signal);
    expect(recorded).toHaveLength(1);
    expect(recorded[0]?.init.signal).toBe(controller.signal);

    controller.abort();
    await expect(loadUnits(controller.signal)).rejects.toThrow();
    expect(requestsTo(recorded, "GET", UNITS)).toHaveLength(0);
  });
});
