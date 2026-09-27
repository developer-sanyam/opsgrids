import { StrictMode } from "react";
import type { ReactNode } from "react";
import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../http";
import { resetPageBudgetForTests } from "./lookups";
import { buildSampleWorkbook } from "./template";
import {
  fileOf,
  fileWithRows,
  loadBlob,
  makeProductEntity,
  makeProductUpdate,
  productColumns,
  productLookups,
  storedProduct,
} from "./testkit";
import type { ProductFilters, ProductItem, ProductPayload } from "./testkit";
import type { ExportPage, ImportSummary } from "./types";
import { useImportExport } from "./useImportExport";

/*
 * A long-running action is started outside `act` and observed with `waitFor`, then settled inside
 * one `act` at the end: two `act` scopes open at once is what React warns about.
 */

const HEADERS = ["SKU *", "Name *", "Price", "Currency", "Category"] as const;

interface Download {
  name: string;
  blob: Blob | undefined;
}

let downloads: Download[];

beforeEach(() => {
  resetPageBudgetForTests();
  downloads = [];
  const blobs = new Map<string, Blob>();
  URL.createObjectURL = vi.fn((blob: Blob) => {
    const url = `blob:test/${blobs.size + 1}`;
    blobs.set(url, blob);
    return url;
  });
  URL.revokeObjectURL = vi.fn();
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function click(this: HTMLAnchorElement) {
    downloads.push({ name: this.download, blob: blobs.get(this.href) });
  });
});

function deferred<T>() {
  let settle: (value: T) => void = () => undefined;
  const promise = new Promise<T>((resolve) => {
    settle = resolve;
  });
  return { promise, resolve: settle };
}

function item(i: number): ProductItem {
  return {
    id: `p-${i}`,
    sku: `SKU-${i}`,
    name: `Product ${i}`,
    status: "in_stock",
    category: "Hardware",
    priceMinor: 100 * i,
    currency: "INR",
    quantity: i,
    active: true,
    email: "orders@example.com",
    createdAt: "2026-09-26T10:30:00Z",
  };
}

/** A list endpoint over `total` items. */
function pagesOf(total: number) {
  return (args: { page: number; size: number }): Promise<ExportPage<ProductItem>> => {
    const start = (args.page - 1) * args.size;
    const count = Math.max(0, Math.min(args.size, total - start));
    return Promise.resolve({
      items: Array.from({ length: count }, (_, i) => item(start + i + 1)),
      total,
      pages: Math.ceil(total / args.size),
    });
  };
}

const validRows = [
  ["A-1", "Bracket", "1499.50", "INR", "Hardware"],
  ["A-2", "Nut", "10", "JPY", "Fasteners"],
];

type Hook = { current: ReturnType<typeof useImportExport> };

async function workbookOf(download: Download | undefined) {
  if (download?.blob === undefined) throw new Error("Nothing was downloaded");
  return loadBlob(download.blob);
}

async function open(result: Hook, file: File): Promise<void> {
  await act(async () => {
    await result.current.actions.openFile(file);
  });
}

async function settle(work: Promise<void>): Promise<void> {
  await act(async () => {
    await work;
  });
}

/** A `create` that waits for the test to let it finish, and records what it was sent. */
function gatedCreate() {
  const gate = deferred<undefined>();
  const create = vi.fn<(payload: ProductPayload) => Promise<undefined>>(() => gate.promise);
  return { gate, create, entity: makeProductEntity({ create: (payload) => create(payload) }) };
}

describe("useImportExport: meta and identity", () => {
  it("describes the entity for the dialog", () => {
    const { result } = renderHook(() => useImportExport(makeProductEntity()));
    expect(result.current.meta).toEqual({
      noun: "products",
      nounSingular: "product",
      maxRows: 1000,
      exportMaxRows: 10_000,
      requiredHeaders: ["SKU", "Name"],
      canExport: true,
      canUpdate: false,
    });
    expect(result.current.state).toMatchObject({ phase: "menu", mode: "add", error: null, preview: null, summary: null });
  });

  it("uses the entity's own limits and reports when it cannot export", () => {
    const entity = makeProductEntity({ maxRows: 50, exportMaxRows: 300 });
    const { result } = renderHook(() => useImportExport(entity));
    expect(result.current.meta).toMatchObject({ maxRows: 50, exportMaxRows: 300 });
    const noExport = renderHook(() => useImportExport({ ...entity, export: undefined }));
    expect(noExport.result.current.meta.canExport).toBe(false);
  });

  it("keeps the same actions across renders, state changes and new filters", async () => {
    const { result, rerender } = renderHook(
      ({ search }: { search: string }) => useImportExport(makeProductEntity(), { filters: { search } }),
      { initialProps: { search: "a" } },
    );
    const first = result.current.actions;
    rerender({ search: "b" });
    expect(result.current.actions).toBe(first);
    await open(result, await fileWithRows([...HEADERS], validRows));
    expect(result.current.state.phase).toBe("preview");
    expect(result.current.actions).toBe(first);
  });
});

describe("useImportExport: reading a file", () => {
  it("previews the rows: valid, invalid with their reasons, and the counts", async () => {
    const { result } = renderHook(() => useImportExport(makeProductEntity()));
    const file = await fileWithRows(
      [...HEADERS],
      [
        ...validRows,
        ["", "No sku", "5", "INR", "Hardware"],
        ["A-3", "Bolt", "1.5", "JPY", "Nowhere"],
      ],
      "products.xlsx",
    );
    await open(result, file);

    const { state } = result.current;
    expect(state.phase).toBe("preview");
    expect(state.error).toBeNull();
    const preview = state.preview;
    expect(preview).toMatchObject({
      fileName: "products.xlsx",
      total: 4,
      valid: 2,
      invalid: 2,
      sample: 0,
      unknownHeaders: [],
      missingRequired: [],
    });
    expect(preview?.columns).toEqual([
      { key: "sku", header: "SKU" },
      { key: "name", header: "Name" },
      { key: "price", header: "Price" },
      { key: "currency", header: "Currency" },
      { key: "category", header: "Category" },
    ]);
    // Invalid rows first, then the rest in sheet order.
    expect(preview?.rows.map((row) => row.row)).toEqual([4, 5, 2, 3]);
    expect(preview?.rows[0]).toMatchObject({ sample: false, error: "SKU is required" });
    expect(preview?.rows[1]?.error).toBe(
      "'Nowhere' is not one of the accepted values for Category (see the Lists sheet); Price must be a whole number for JPY, which has no decimal places",
    );
    expect(preview?.rows[2]).toMatchObject({
      error: null,
      values: { sku: "A-1", name: "Bracket", price: "1499.50", currency: "INR", category: "Hardware" },
    });
  });

  it("is reading while the lists load, then previews", async () => {
    const lookups = deferred<typeof productLookups>();
    const entity = makeProductEntity({ loadLookups: () => lookups.promise });
    const { result } = renderHook(() => useImportExport(entity));
    const file = await fileWithRows([...HEADERS], validRows);
    const reading = result.current.actions.openFile(file);
    await waitFor(() => expect(result.current.state.phase).toBe("reading"));
    expect(result.current.state).toMatchObject({ preview: null, error: null });
    lookups.resolve(productLookups);
    await settle(reading);
    expect(result.current.state.phase).toBe("preview");
  });

  it("recognises the unchanged rows of the sample sheet and does not count them as wrong", async () => {
    const entity = makeProductEntity();
    const { result } = renderHook(() => useImportExport(entity));
    const workbook = await loadBlob(await buildSampleWorkbook(entity, productLookups));
    await open(result, await fileOf(workbook, "products-sample.xlsx"));
    expect(result.current.state.preview).toMatchObject({ total: 2, sample: 2, valid: 0, invalid: 0 });
    expect(result.current.state.preview?.rows.every((row) => row.sample && row.error === null)).toBe(true);
  });

  it("lists at most 200 rows, invalid ones first, while counting them all", async () => {
    const rows = Array.from({ length: 250 }, (_, i) => [`S-${i}`, i >= 245 ? "" : `Name ${i}`, "", "", ""]);
    const { result } = renderHook(() => useImportExport(makeProductEntity()));
    await open(result, await fileWithRows([...HEADERS], rows));
    const preview = result.current.state.preview;
    expect(preview).toMatchObject({ total: 250, valid: 245, invalid: 5 });
    expect(preview?.rows).toHaveLength(200);
    expect(preview?.rows.slice(0, 5).every((row) => row.error === "Name is required")).toBe(true);
    expect(preview?.rows[5]?.error).toBeNull();
  });

  it("reports unknown headers and required headers the file lacks, and will not start", async () => {
    const { result } = renderHook(() => useImportExport(makeProductEntity()));
    await open(result, await fileWithRows(["Name *", "Colour"], [["Bracket", "blue"]]));
    expect(result.current.state.phase).toBe("preview");
    expect(result.current.state.preview).toMatchObject({ unknownHeaders: ["Colour"], missingRequired: ["SKU"] });

    await act(async () => {
      await result.current.actions.startImport();
    });
    expect(result.current.state.phase).toBe("preview");
  });

  it("goes to the error phase, with the way out, for a file that is not .xlsx", async () => {
    const { result } = renderHook(() => useImportExport(makeProductEntity()));
    await open(result, new File(["a,b"], "products.csv"));
    expect(result.current.state.phase).toBe("error");
    expect(result.current.state.error).toContain("Save As → Excel Workbook (.xlsx)");
    expect(result.current.state.preview).toBeNull();
  });

  it("goes to the error phase for a file over 5 MB", async () => {
    const { result } = renderHook(() => useImportExport(makeProductEntity()));
    await open(result, new File([new Uint8Array(5 * 1024 * 1024 + 1)], "big.xlsx"));
    expect(result.current.state.phase).toBe("error");
    expect(result.current.state.error).toContain("limit is 5 MB");
  });

  it("goes to the error phase for more rows than the entity allows, naming the count", async () => {
    const { result } = renderHook(() => useImportExport(makeProductEntity({ maxRows: 2 })));
    await open(result, await fileWithRows([...HEADERS], [...validRows, ["A-3", "Bolt", "", "", ""]]));
    expect(result.current.state.phase).toBe("error");
    expect(result.current.state.error).toContain("The file has 3 rows");
    expect(result.current.state.error).toContain("limit is 2");
  });

  it("goes to the error phase when the lists it is checked against cannot be loaded", async () => {
    const entity = makeProductEntity({
      loadLookups: () => Promise.reject(new ApiError(403, "Forbidden", { detail: "You may not list categories" })),
    });
    const { result } = renderHook(() => useImportExport(entity));
    await open(result, await fileWithRows([...HEADERS], validRows));
    expect(result.current.state.phase).toBe("error");
    expect(result.current.state.error).toBe(
      "The lists this file is checked against could not be loaded. You may not list categories",
    );
  });

  it("recovers from an error: choosing another file starts again, and reset returns to the menu", async () => {
    const { result } = renderHook(() => useImportExport(makeProductEntity()));
    await open(result, new File(["x"], "notes.txt"));
    expect(result.current.state.phase).toBe("error");
    await open(result, await fileWithRows([...HEADERS], validRows));
    expect(result.current.state).toMatchObject({ phase: "preview", error: null });
    act(() => result.current.actions.reset());
    expect(result.current.state).toMatchObject({ phase: "menu", preview: null, error: null });
  });

  it("drops a slow read that a newer choice has overtaken", async () => {
    const slow = deferred<typeof productLookups>();
    const loadLookups = vi.fn(() => slow.promise);
    const entity = makeProductEntity({ loadLookups });
    const { result } = renderHook(() => useImportExport(entity));
    const older = await fileWithRows([...HEADERS], [["OLD-1", "Old", "", "", ""]], "old.xlsx");
    const newer = await fileWithRows([...HEADERS], validRows, "new.xlsx");

    const first = result.current.actions.openFile(older);
    await waitFor(() => expect(loadLookups).toHaveBeenCalledTimes(1));
    // The newer choice waits on the same lists load rather than asking again.
    const second = result.current.actions.openFile(newer);
    await waitFor(() => expect(result.current.state.phase).toBe("reading"));

    slow.resolve(productLookups);
    await settle(Promise.all([first, second]).then(() => undefined));
    expect(loadLookups).toHaveBeenCalledTimes(1);
    expect(result.current.state.preview?.fileName).toBe("new.xlsx");
  });

  it("drops a slow read that a reset has overtaken", async () => {
    const slow = deferred<typeof productLookups>();
    const entity = makeProductEntity({ loadLookups: () => slow.promise });
    const { result } = renderHook(() => useImportExport(entity));
    const file = await fileWithRows([...HEADERS], validRows);
    const reading = result.current.actions.openFile(file);
    await waitFor(() => expect(result.current.state.phase).toBe("reading"));
    act(() => result.current.actions.reset());
    slow.resolve(productLookups);
    await settle(reading);
    expect(result.current.state).toMatchObject({ phase: "menu", preview: null });
  });
});

describe("useImportExport: importing", () => {
  it("sends the valid rows in order, then reports what happened, and downloads the result", async () => {
    const sent: ProductPayload[] = [];
    const entity = makeProductEntity({
      create: (payload) => {
        sent.push(payload);
        return Promise.resolve({ id: `id-${payload.sku}` });
      },
    });
    const { result } = renderHook(() => useImportExport(entity));
    await open(
      result,
      await fileWithRows([...HEADERS], [validRows[0] ?? [], ["", "No sku", "5", "INR", ""], validRows[1] ?? []]),
    );
    expect(result.current.state.preview).toMatchObject({ valid: 2, invalid: 1 });

    const importing = result.current.actions.startImport();
    await waitFor(() => expect(result.current.state.phase).toBe("importing"));
    expect(result.current.state.progress).toMatchObject({ total: 2 });
    await settle(importing);

    expect(sent.map((p) => [p.sku, p.priceMinor, p.currency, p.categoryId])).toEqual([
      ["A-1", 149950, "INR", "cat-1"],
      ["A-2", 10, "JPY", "cat-2"],
    ]);
    const { state } = result.current;
    expect(state.phase).toBe("done");
    expect(state.progress).toBeNull();
    expect(state.summary).toMatchObject({ created: 2, skipped: 0, failed: 0, invalid: 1, cancelled: false });
    expect(state.summary?.outcomes.map((o) => [o.row, o.status, o.id, o.label])).toEqual([
      [2, "created", "id-A-1", "A-1"],
      [3, "invalid", undefined, ""],
      [4, "created", "id-A-2", "A-2"],
    ]);
    expect(state.summary?.outcomes[1]?.message).toBe("SKU is required");

    await act(async () => {
      await result.current.actions.downloadReport();
    });
    expect(downloads).toHaveLength(1);
    expect(downloads[0]?.name).toBe("products-import-result.xlsx");
    const sheet = (await workbookOf(downloads[0])).getWorksheet("Result");
    expect([2, 3, 4].map((r) => sheet?.getCell(r, 3).value)).toEqual(["Created", "Invalid", "Created"]);
  }, 10_000);

  it("skips a row the server says exists, fails one it refuses, and carries on", async () => {
    const entity = makeProductEntity({
      create: (payload) => {
        if (payload.sku === "A-1") {
          return Promise.reject(new ApiError(409, "conflict", { detail: "SKU A-1 already exists" }));
        }
        return Promise.reject(new ApiError(403, "forbidden", { detail: "You may not create products" }));
      },
    });
    const { result } = renderHook(() => useImportExport(entity));
    await open(result, await fileWithRows([...HEADERS], validRows));
    await act(async () => {
      await result.current.actions.startImport();
    });
    expect(result.current.state.summary).toMatchObject({ created: 0, skipped: 1, failed: 1, invalid: 0 });
    expect(result.current.state.summary?.outcomes.map((o) => [o.status, o.message])).toEqual([
      ["skipped", "SKU A-1 already exists"],
      ["failed", "You may not create products"],
    ]);
  }, 10_000);

  it("does not send the sample rows", async () => {
    const create = vi.fn<(payload: ProductPayload) => Promise<undefined>>(() => Promise.resolve(undefined));
    const entity = makeProductEntity({ create: (payload) => create(payload) });
    const { result } = renderHook(() => useImportExport(entity));
    const workbook = await loadBlob(await buildSampleWorkbook(entity, productLookups));
    const sheet = workbook.getWorksheet("Import");
    if (sheet === undefined) throw new Error("No Import sheet");
    sheet.getCell("A4").value = "REAL-1";
    sheet.getCell("B4").value = "A real product";
    await open(result, await fileOf(workbook));
    expect(result.current.state.preview).toMatchObject({ total: 3, sample: 2, valid: 1 });
    await act(async () => {
      await result.current.actions.startImport();
    });
    expect(create).toHaveBeenCalledTimes(1);
    expect(result.current.state.summary?.outcomes).toHaveLength(1);
    expect(result.current.state.summary).toMatchObject({ created: 1, invalid: 0 });
  }, 10_000);

  it("ignores a second start while the first is running, so no row is created twice", async () => {
    const { gate, create, entity } = gatedCreate();
    const { result } = renderHook(() => useImportExport(entity));
    await open(result, await fileWithRows([...HEADERS], [validRows[0] ?? []]));

    const first = result.current.actions.startImport();
    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    await result.current.actions.startImport();
    gate.resolve(undefined);
    await settle(first);
    expect(create).toHaveBeenCalledTimes(1);
    expect(result.current.state.summary?.created).toBe(1);
  });

  it("cancels after the row in flight and says so in the summary", async () => {
    const { gate, create, entity } = gatedCreate();
    const { result } = renderHook(() => useImportExport(entity));
    await open(result, await fileWithRows([...HEADERS], [...validRows, ["A-3", "Bolt", "", "", ""]]));

    const running = result.current.actions.startImport();
    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    result.current.actions.cancelImport();
    gate.resolve(undefined);
    await settle(running);

    expect(create).toHaveBeenCalledTimes(1);
    expect(result.current.state.phase).toBe("done");
    expect(result.current.state.summary).toMatchObject({ created: 1, cancelled: true });
    expect(result.current.state.summary?.outcomes).toHaveLength(1);
  });

  it("finishes into done when reset is called mid-import, so rows already sent are not lost from view", async () => {
    const { gate, create, entity } = gatedCreate();
    const { result } = renderHook(() => useImportExport(entity));
    await open(result, await fileWithRows([...HEADERS], validRows));
    const running = result.current.actions.startImport();
    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(result.current.state.phase).toBe("importing"));
    result.current.actions.reset();
    expect(result.current.state.phase).toBe("importing");
    gate.resolve(undefined);
    await settle(running);
    expect(result.current.state.phase).toBe("done");
    expect(result.current.state.summary).toMatchObject({ created: 1, cancelled: true });
    act(() => result.current.actions.reset());
    expect(result.current.state.phase).toBe("menu");
  });

  it("warns before the tab closes while importing, and only then", async () => {
    const { gate, create, entity } = gatedCreate();
    const { result } = renderHook(() => useImportExport(entity));
    const closing = (): boolean => {
      const event = new Event("beforeunload", { cancelable: true });
      window.dispatchEvent(event);
      return event.defaultPrevented;
    };

    expect(closing()).toBe(false);
    await open(result, await fileWithRows([...HEADERS], [validRows[0] ?? []]));
    expect(closing()).toBe(false);

    const running = result.current.actions.startImport();
    await waitFor(() => expect(result.current.state.phase).toBe("importing"));
    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    expect(closing()).toBe(true);
    gate.resolve(undefined);
    await settle(running);
    expect(closing()).toBe(false);
  });

  it("stops sending when the component unmounts mid-import", async () => {
    const { gate, create, entity } = gatedCreate();
    const { result, unmount } = renderHook(() => useImportExport(entity));
    await open(result, await fileWithRows([...HEADERS], validRows));
    const running = result.current.actions.startImport();
    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    unmount();
    gate.resolve(undefined);
    await running;
    expect(create).toHaveBeenCalledTimes(1);
  });

  it("does nothing when there is no preview to send", async () => {
    const { result } = renderHook(() => useImportExport(makeProductEntity()));
    await act(async () => {
      await result.current.actions.startImport();
    });
    expect(result.current.state.phase).toBe("menu");
  });
});

describe("useImportExport: the sample sheet", () => {
  it("downloads it, built from the loaded lists", async () => {
    const { result } = renderHook(() => useImportExport(makeProductEntity()));
    await act(async () => {
      await result.current.actions.downloadTemplate();
    });
    expect(downloads).toHaveLength(1);
    expect(downloads[0]?.name).toBe("products-sample.xlsx");
    const workbook = await workbookOf(downloads[0]);
    expect(workbook.worksheets.map((s) => s.name)).toEqual(["Import", "Instructions", "Lists"]);
    expect(workbook.getWorksheet("Lists")?.getCell("A2").value).toBe("Hardware");
    expect(result.current.state).toMatchObject({ downloadingTemplate: false, notice: null });
  });

  it("shows it is working and ignores a second click meanwhile", async () => {
    const lookups = deferred<typeof productLookups>();
    const loadLookups = vi.fn(() => lookups.promise);
    const { result } = renderHook(() => useImportExport(makeProductEntity({ loadLookups })));
    const first = result.current.actions.downloadTemplate();
    await waitFor(() => expect(result.current.state.downloadingTemplate).toBe(true));
    await result.current.actions.downloadTemplate();
    expect(loadLookups).toHaveBeenCalledTimes(1);
    lookups.resolve(productLookups);
    await settle(first);
    expect(downloads).toHaveLength(1);
    expect(result.current.state.downloadingTemplate).toBe(false);
  });

  it("puts the reason in the notice when it cannot be built, and never throws", async () => {
    const entity = makeProductEntity({
      loadLookups: () => Promise.reject(new ApiError(403, "forbidden", { detail: "You may not read categories" })),
    });
    const { result } = renderHook(() => useImportExport(entity));
    await act(async () => {
      await result.current.actions.downloadTemplate();
    });
    expect(result.current.state.notice).toBe("The sample sheet could not be created. You may not read categories");
    expect(result.current.state.downloadingTemplate).toBe(false);
    expect(downloads).toEqual([]);
  });

  it("clears the notice on the next attempt", async () => {
    let fail = true;
    const entity = makeProductEntity({
      loadLookups: () => (fail ? Promise.reject(new Error("boom")) : Promise.resolve(productLookups)),
    });
    const { result } = renderHook(() => useImportExport(entity));
    await act(async () => {
      await result.current.actions.downloadTemplate();
    });
    expect(result.current.state.notice).toBe("The sample sheet could not be created. Try again.");
    fail = false;
    await act(async () => {
      await result.current.actions.downloadTemplate();
    });
    expect(result.current.state.notice).toBeNull();
    expect(downloads).toHaveLength(1);
  });
});

describe("useImportExport: exporting", () => {
  it("pages through the whole filtered list, reports progress, and downloads one file", async () => {
    const gates = [deferred<undefined>(), deferred<undefined>(), deferred<undefined>()];
    const fetchPage = vi.fn(async (args: { page: number; size: number; filters: ProductFilters }) => {
      await gates[args.page - 1]?.promise;
      return pagesOf(450)(args);
    });
    const { result } = renderHook(() =>
      useImportExport(makeProductEntity({ fetchPage }), { filters: { search: "steel" } }),
    );
    const exporting = result.current.actions.exportAll();

    await waitFor(() => expect(fetchPage).toHaveBeenCalledTimes(1));
    expect(result.current.state.exporting).toEqual({ fetched: 0, total: 0 });
    gates[0]?.resolve(undefined);
    await waitFor(() => expect(result.current.state.exporting).toEqual({ fetched: 200, total: 450 }));
    gates[1]?.resolve(undefined);
    await waitFor(() => expect(result.current.state.exporting).toEqual({ fetched: 400, total: 450 }));
    gates[2]?.resolve(undefined);
    await settle(exporting);

    expect(fetchPage.mock.calls.map(([args]) => [args.page, args.size, args.filters])).toEqual([
      [1, 200, { search: "steel" }],
      [2, 200, { search: "steel" }],
      [3, 200, { search: "steel" }],
    ]);
    expect(result.current.state).toMatchObject({ exporting: null, notice: null });
    expect(downloads).toHaveLength(1);
    expect(downloads[0]?.name).toMatch(/^products-\d{4}-\d{2}-\d{2}\.xlsx$/);
    const sheet = (await workbookOf(downloads[0])).getWorksheet("Export");
    expect(sheet?.actualRowCount).toBe(451);
    expect(sheet?.getCell("B451").value).toBe("SKU-450");
  }, 10_000);

  it("uses the filters the screen has when the export starts", async () => {
    const fetchPage = vi.fn((args: { page: number; size: number; filters: ProductFilters }) => pagesOf(10)(args));
    const { result, rerender } = renderHook(
      ({ search }: { search: string }) => useImportExport(makeProductEntity({ fetchPage }), { filters: { search } }),
      { initialProps: { search: "" } },
    );
    rerender({ search: "bracket" });
    await act(async () => {
      await result.current.actions.exportAll();
    });
    expect(fetchPage.mock.calls[0]?.[0].filters).toEqual({ search: "bracket" });
  });

  it("stops at the entity's row cap, downloads what it has and says the export was cut short", async () => {
    const fetchPage = vi.fn(pagesOf(1000));
    const { result } = renderHook(() => useImportExport(makeProductEntity({ fetchPage, exportMaxRows: 300 })));
    await act(async () => {
      await result.current.actions.exportAll();
    });
    expect(fetchPage).toHaveBeenCalledTimes(2);
    expect(result.current.state.notice).toBe(
      "Only the first 300 of 1,000 products were exported. Narrow the filters to export the rest.",
    );
    const sheet = (await workbookOf(downloads[0])).getWorksheet("Export");
    expect(sheet?.actualRowCount).toBe(301);
  });

  it("downloads a header-only file for an empty list", async () => {
    const { result } = renderHook(() => useImportExport(makeProductEntity({ fetchPage: pagesOf(0) })));
    await act(async () => {
      await result.current.actions.exportAll();
    });
    const sheet = (await workbookOf(downloads[0])).getWorksheet("Export");
    expect(sheet?.actualRowCount).toBe(1);
  });

  it("cancels: no file, no notice, and the fetch is told to stop", async () => {
    let signal: AbortSignal | undefined;
    const fetchPage = vi.fn(
      (args: { signal: AbortSignal }) =>
        new Promise<ExportPage<ProductItem>>((_, reject) => {
          signal = args.signal;
          args.signal.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
        }),
    );
    const { result } = renderHook(() => useImportExport(makeProductEntity({ fetchPage })));
    const exporting = result.current.actions.exportAll();
    await waitFor(() => expect(fetchPage).toHaveBeenCalled());
    result.current.actions.cancelExport();
    await settle(exporting);
    expect(signal?.aborted).toBe(true);
    expect(downloads).toEqual([]);
    expect(result.current.state).toMatchObject({ exporting: null, notice: null });
  });

  it("puts the server's reason in the notice when a page fails, and never throws", async () => {
    const fetchPage = vi.fn(() =>
      Promise.reject(new ApiError(403, "forbidden", { detail: "You may not read products" })),
    );
    const { result } = renderHook(() => useImportExport(makeProductEntity({ fetchPage })));
    await act(async () => {
      await result.current.actions.exportAll();
    });
    expect(result.current.state.notice).toBe("The products could not be exported. You may not read products");
    expect(result.current.state.exporting).toBeNull();
    expect(downloads).toEqual([]);
  });

  it("ignores a second export while one is running", async () => {
    const gate = deferred<undefined>();
    const fetchPage = vi.fn(async (args: { page: number; size: number }) => {
      await gate.promise;
      return pagesOf(5)(args);
    });
    const { result } = renderHook(() => useImportExport(makeProductEntity({ fetchPage })));
    const first = result.current.actions.exportAll();
    await waitFor(() => expect(result.current.state.exporting).not.toBeNull());
    await result.current.actions.exportAll();
    gate.resolve(undefined);
    await settle(first);
    expect(fetchPage).toHaveBeenCalledTimes(1);
    expect(downloads).toHaveLength(1);
  });

  it("does nothing for an entity that cannot export", async () => {
    const { result } = renderHook(() => useImportExport({ ...makeProductEntity(), export: undefined }));
    await act(async () => {
      await result.current.actions.exportAll();
    });
    expect(result.current.state.exporting).toBeNull();
    expect(downloads).toEqual([]);
  });

  it("aborts the fetch when the component unmounts", async () => {
    let signal: AbortSignal | undefined;
    const fetchPage = vi.fn(
      (args: { signal: AbortSignal }) =>
        new Promise<ExportPage<ProductItem>>((_, reject) => {
          signal = args.signal;
          args.signal.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
        }),
    );
    const { result, unmount } = renderHook(() => useImportExport(makeProductEntity({ fetchPage })));
    const exporting = result.current.actions.exportAll();
    await waitFor(() => expect(fetchPage).toHaveBeenCalled());
    unmount();
    await exporting;
    expect(signal?.aborted).toBe(true);
    expect(downloads).toEqual([]);
  });
});

describe("useImportExport: the lists are loaded once per dialog session", () => {
  it("shares one load between opening a file, the sample sheet and an export, and reloads after reset", async () => {
    const loadLookups = vi.fn(() => Promise.resolve(productLookups));
    const entity = makeProductEntity({ loadLookups });
    const { result } = renderHook(() => useImportExport(entity));
    await open(result, await fileWithRows([...HEADERS], validRows));
    await act(async () => {
      await result.current.actions.downloadTemplate();
    });
    await act(async () => {
      await result.current.actions.exportAll();
    });
    expect(loadLookups).toHaveBeenCalledTimes(1);
    expect(downloads).toHaveLength(2);

    act(() => result.current.actions.reset());
    await act(async () => {
      await result.current.actions.downloadTemplate();
    });
    expect(loadLookups).toHaveBeenCalledTimes(2);
  });

  it("loads again after an import that created a row", async () => {
    const loadLookups = vi.fn(() => Promise.resolve(productLookups));
    const entity = makeProductEntity({ loadLookups });
    const { result } = renderHook(() => useImportExport(entity));
    await open(result, await fileWithRows([...HEADERS], [validRows[0] ?? []]));
    await act(async () => {
      await result.current.actions.startImport();
    });
    await act(async () => {
      await result.current.actions.downloadTemplate();
    });
    expect(loadLookups).toHaveBeenCalledTimes(2);
  });
});

describe("useImportExport: exporting a list that pages unevenly", () => {
  const range = (from: number, to: number): ProductItem[] =>
    Array.from({ length: to - from + 1 }, (_, i) => item(from + i));

  async function exportWith(
    pages: (page: number) => ExportPage<ProductItem>,
    entityOptions: Parameters<typeof makeProductEntity>[0] = {},
  ) {
    const fetchPage = vi.fn((args: { page: number }) => Promise.resolve(pages(args.page)));
    const hook = renderHook(() => useImportExport(makeProductEntity({ fetchPage, ...entityOptions })));
    await act(async () => {
      await hook.result.current.actions.exportAll();
    });
    const sheet = (await workbookOf(downloads[0])).getWorksheet("Export");
    return { ...hook, sheet, fetchPage };
  }

  it("writes a row the list returned twice once, and says the export came up short", async () => {
    // Page 2 starts ten rows early (repeating SKU-191..200) and so never reaches SKU-291..300.
    const { result, sheet } = await exportWith((page) =>
      page === 1
        ? { items: range(1, 200), total: 300, pages: 2 }
        : { items: range(191, 290), total: 300, pages: 2 },
    );
    expect(sheet?.actualRowCount).toBe(291);
    const skus = Array.from({ length: 290 }, (_, i) => sheet?.getCell(i + 2, 2).value);
    expect(new Set(skus).size).toBe(290);
    expect(result.current.state.notice).toBe(
      "Exported 290 of 300 rows. The list changed or paged unevenly while exporting: run the export again, or narrow the filters.",
    );
    expect(downloads).toHaveLength(1);
  });

  it("says so when the list shrinks while it is being read", async () => {
    const { result, sheet } = await exportWith((page) =>
      page === 1 ? { items: range(1, 200), total: 250, pages: 2 } : { items: range(201, 230), total: 250, pages: 2 },
    );
    expect(sheet?.actualRowCount).toBe(231);
    expect(result.current.state.notice).toBe(
      "Exported 230 of 250 rows. The list changed or paged unevenly while exporting: run the export again, or narrow the filters.",
    );
  });

  it("stays quiet when every row arrived exactly once", async () => {
    const { result, sheet } = await exportWith((page) =>
      page === 1 ? { items: range(1, 200), total: 250, pages: 2 } : { items: range(201, 250), total: 250, pages: 2 },
    );
    expect(sheet?.actualRowCount).toBe(251);
    expect(result.current.state.notice).toBeNull();
  });

  it("does not repeat rows when the entity has no id column to tell them apart, and does not complain", async () => {
    const withoutId = productColumns.filter((column) => column.key !== "id");
    const { result, sheet } = await exportWith(
      (page) =>
        page === 1
          ? { items: range(1, 200), total: 300, pages: 2 }
          : { items: range(191, 290), total: 300, pages: 2 },
      { columns: withoutId },
    );
    // 200 + 100 rows arrived, which is what the list said there were: nothing to detect.
    expect(sheet?.actualRowCount).toBe(301);
    expect(result.current.state.notice).toBeNull();
  });

  it("reports the cap as the reason when it cut the export, not an uneven list", async () => {
    const { result } = await exportWith(
      (page) => ({ items: range((page - 1) * 200 + 1, page * 200), total: 1000, pages: 5 }),
      { exportMaxRows: 300 },
    );
    expect(result.current.state.notice).toBe(
      "Only the first 300 of 1,000 products were exported. Narrow the filters to export the rest.",
    );
  });

  it("uses an uneven-list notice ahead of the cap notice when the export is short as well", async () => {
    const { result } = await exportWith(
      (page) =>
        page === 1
          ? { items: range(1, 200), total: 1000, pages: 5 }
          : { items: range(191, 290), total: 1000, pages: 5 },
      { exportMaxRows: 300 },
    );
    expect(result.current.state.notice).toBe(
      "Exported 290 of 300 rows. The list changed or paged unevenly while exporting: run the export again, or narrow the filters.",
    );
  });
});

describe("useImportExport: closing the dialog", () => {
  it("is clean after cancelExport then reset, and the late finish of the old export does nothing", async () => {
    const gate = deferred<undefined>();
    let calls = 0;
    const fetchPage = vi.fn(async (args: { page: number; size: number }) => {
      calls += 1;
      if (calls === 1) await gate.promise;
      return pagesOf(5)(args);
    });
    const { result } = renderHook(() => useImportExport(makeProductEntity({ fetchPage })));
    const first = result.current.actions.exportAll();
    await waitFor(() => expect(result.current.state.exporting).not.toBeNull());

    act(() => {
      result.current.actions.cancelExport();
      result.current.actions.reset();
    });
    expect(result.current.state).toMatchObject({ phase: "menu", exporting: null, notice: null, preview: null });

    // A new export may start straight away, and the old one finishing late must not touch it.
    const second = result.current.actions.exportAll();
    await settle(second);
    expect(downloads).toHaveLength(1);
    gate.resolve(undefined);
    await settle(first);
    expect(downloads).toHaveLength(1);
    expect(result.current.state).toMatchObject({ exporting: null, notice: null });
  });

  it("leaves nothing behind when reset arrives while the file is still being read", async () => {
    const slow = deferred<typeof productLookups>();
    const entity = makeProductEntity({ loadLookups: () => slow.promise });
    const { result } = renderHook(() => useImportExport(entity));
    const reading = result.current.actions.openFile(await fileWithRows([...HEADERS], validRows));
    await waitFor(() => expect(result.current.state.phase).toBe("reading"));
    act(() => result.current.actions.reset());
    slow.resolve(productLookups);
    await settle(reading);
    expect(result.current.state).toMatchObject({ phase: "menu", preview: null, error: null });
    // Opening the dialog again starts from the menu, and the next file previews normally.
    await open(result, await fileWithRows([...HEADERS], validRows, "second.xlsx"));
    expect(result.current.state.preview?.fileName).toBe("second.xlsx");
  });
});

describe("useImportExport: telling the screen", () => {
  const oneRow = [validRows[0] ?? []];

  it("calls onImported once, with the summary, when a run ends having created a row", async () => {
    const onImported = vi.fn<(summary: ImportSummary) => void>();
    const { result } = renderHook(() => useImportExport(makeProductEntity(), { onImported }));
    await open(result, await fileWithRows([...HEADERS], oneRow));
    await act(async () => {
      await result.current.actions.startImport();
    });
    expect(onImported).toHaveBeenCalledTimes(1);
    expect(onImported).toHaveBeenCalledWith(result.current.state.summary);
    expect(result.current.state.summary).toMatchObject({ created: 1 });
  });

  it("does not call it when nothing was created", async () => {
    const onImported = vi.fn<(summary: ImportSummary) => void>();
    const entity = makeProductEntity({
      create: () => Promise.reject(new ApiError(409, "conflict", { detail: "Already exists" })),
    });
    const { result } = renderHook(() => useImportExport(entity, { onImported }));
    await open(result, await fileWithRows([...HEADERS], oneRow));
    await act(async () => {
      await result.current.actions.startImport();
    });
    expect(result.current.state.phase).toBe("done");
    expect(onImported).not.toHaveBeenCalled();
  });

  it("calls it for a cancelled run that had already created a row", async () => {
    const onImported = vi.fn<(summary: ImportSummary) => void>();
    const { gate, create, entity } = gatedCreate();
    const { result } = renderHook(() => useImportExport(entity, { onImported }));
    await open(result, await fileWithRows([...HEADERS], validRows));
    const running = result.current.actions.startImport();
    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    result.current.actions.cancelImport();
    gate.resolve(undefined);
    await settle(running);
    expect(onImported).toHaveBeenCalledTimes(1);
    expect(onImported.mock.calls[0]?.[0]).toMatchObject({ created: 1, cancelled: true });
  });

  it("calls the latest callback, not the one from the render that opened the dialog", async () => {
    const before = vi.fn<(summary: ImportSummary) => void>();
    const after = vi.fn<(summary: ImportSummary) => void>();
    const { result, rerender } = renderHook(
      ({ onImported }: { onImported: (summary: ImportSummary) => void }) =>
        useImportExport(makeProductEntity(), { onImported }),
      { initialProps: { onImported: before } },
    );
    await open(result, await fileWithRows([...HEADERS], oneRow));
    rerender({ onImported: after });
    await act(async () => {
      await result.current.actions.startImport();
    });
    expect(before).not.toHaveBeenCalled();
    expect(after).toHaveBeenCalledTimes(1);
  });

  it("does not call it once the component is gone", async () => {
    const onImported = vi.fn<(summary: ImportSummary) => void>();
    const { gate, create, entity } = gatedCreate();
    const { result, unmount } = renderHook(() => useImportExport(entity, { onImported }));
    await open(result, await fileWithRows([...HEADERS], oneRow));
    const running = result.current.actions.startImport();
    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    unmount();
    gate.resolve(undefined);
    await running;
    expect(onImported).not.toHaveBeenCalled();
  });

});

describe("useImportExport: the result file", () => {
  it("puts a notice on the state when it cannot be made, and clears it on the next attempt", async () => {
    const { result } = renderHook(() => useImportExport(makeProductEntity()));
    await open(result, await fileWithRows([...HEADERS], [validRows[0] ?? []]));
    await act(async () => {
      await result.current.actions.startImport();
    });
    expect(result.current.state.phase).toBe("done");

    URL.createObjectURL = vi.fn(() => {
      throw new Error("no memory");
    });
    await act(async () => {
      await result.current.actions.downloadReport();
    });
    expect(result.current.state.notice).toBe("The result file could not be created. Try again.");
    expect(result.current.state.phase).toBe("done");

    URL.createObjectURL = vi.fn(() => "blob:ok");
    await act(async () => {
      await result.current.actions.downloadReport();
    });
    expect(result.current.state.notice).toBeNull();
    expect(downloads).toHaveLength(1);
  });

  it("does nothing before there is a summary", async () => {
    const { result } = renderHook(() => useImportExport(makeProductEntity()));
    await act(async () => {
      await result.current.actions.downloadReport();
    });
    expect(downloads).toEqual([]);
    expect(result.current.state.notice).toBeNull();
  });
});

describe("useImportExport: React strict mode", () => {
  it("survives the mount, unmount and mount that strict mode does, and still works", async () => {
    const wrapper = ({ children }: { children: ReactNode }) => <StrictMode>{children}</StrictMode>;
    const { result } = renderHook(() => useImportExport(makeProductEntity()), { wrapper });
    await open(result, await fileWithRows([...HEADERS], validRows));
    expect(result.current.state.phase).toBe("preview");
    await act(async () => {
      await result.current.actions.downloadTemplate();
    });
    expect(downloads).toHaveLength(1);
  });
});

describe("useImportExport: update mode", () => {
  const updatable = (send: () => Promise<void> = () => Promise.resolve()) =>
    makeProductEntity({ recordIdKey: "id", update: makeProductUpdate({ items: [storedProduct(1)], send }) });
  const updateRows = [["p-1", "SKU-1", "Bracket"]];

  it("offers the mode only for a list that has an update spec, and starts in add", () => {
    const { result } = renderHook(() => useImportExport(updatable()));
    expect(result.current.meta.canUpdate).toBe(true);
    expect(result.current.state.mode).toBe("add");
    expect(renderHook(() => useImportExport(makeProductEntity())).result.current.meta.canUpdate).toBe(false);
  });

  it("switches the mode, checks the file that is open again, and reports what an import updated", async () => {
    const onImported = vi.fn<(summary: ImportSummary) => void>();
    const { result } = renderHook(() => useImportExport(updatable(), { onImported }));
    await open(result, await fileWithRows(["Id", "SKU *", "Name *"], updateRows));
    expect(result.current.state.preview).toMatchObject({ mode: "add", valid: 0, invalid: 1 });

    await act(async () => {
      await result.current.actions.setMode("addAndUpdate");
    });
    expect(result.current.state.mode).toBe("addAndUpdate");
    expect(result.current.state.preview).toMatchObject({ mode: "addAndUpdate", update: 1, valid: 1 });

    await settle(result.current.actions.startImport());
    expect(result.current.state.summary).toMatchObject({ updated: 1, created: 0 });
    expect(onImported).toHaveBeenCalledTimes(1);
    expect(onImported.mock.calls[0]?.[0]).toMatchObject({ updated: 1 });
  });

  it("gives the actions one identity across the mode change", async () => {
    const { result } = renderHook(() => useImportExport(updatable()));
    const before = result.current.actions;
    await act(async () => {
      await result.current.actions.setMode("addAndUpdate");
    });
    expect(result.current.actions).toBe(before);
  });
});
