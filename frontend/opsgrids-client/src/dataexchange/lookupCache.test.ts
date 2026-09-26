import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../http";
import { createExchange } from "./exchange";
import { fileWithRows, makeProductEntity, productLookups } from "./testkit";
import type { ProductFilters, ProductItem, ProductPayload } from "./testkit";
import type { DataExchangeState, ImportEntity, LookupSet } from "./types";

/*
 * The reference lists cost real requests from a budget shared with everything else the user does,
 * so a dialog session loads them once and every action in it (opening a file, downloading the
 * sample sheet, exporting) uses that one load.
 */

const HEADERS = ["SKU *", "Name *"];
const oneRow = [["A-1", "Bracket"]];

beforeEach(() => {
  URL.createObjectURL = vi.fn(() => "blob:test");
  URL.revokeObjectURL = vi.fn();
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
});

function deferred<T>() {
  let settle: (value: T) => void = () => undefined;
  let fail: (error: unknown) => void = () => undefined;
  const promise = new Promise<T>((resolve, reject) => {
    settle = resolve;
    fail = reject;
  });
  return { promise, resolve: settle, reject: fail };
}

type Product = ImportEntity<ProductPayload, ProductItem, ProductFilters>;

function setUp(loadLookups: (signal: AbortSignal) => Promise<LookupSet>, create?: Product["create"]) {
  let entity: Product = makeProductEntity({ loadLookups, ...(create ? { create } : {}) });
  const states: DataExchangeState[] = [];
  const exchange = createExchange({
    entity: () => entity,
    filters: () => undefined,
    publish: (state) => states.push(state),
    imported: () => undefined,
  });
  return {
    exchange,
    states,
    latest: () => states.at(-1),
    swap: (next: Product) => {
      entity = next;
    },
  };
}

const opened = (file: File, exchange: ReturnType<typeof setUp>["exchange"]) => exchange.actions.openFile(file);

describe("lookups are loaded once per dialog session", () => {
  it("shares one load between opening a file, the sample sheet and an export", async () => {
    const load = vi.fn(() => Promise.resolve(productLookups));
    const { exchange, latest } = setUp(load);
    await opened(await fileWithRows(HEADERS, oneRow), exchange);
    await exchange.actions.downloadTemplate();
    await exchange.actions.exportAll();
    await opened(await fileWithRows(HEADERS, oneRow), exchange);
    expect(load).toHaveBeenCalledTimes(1);
    expect(latest()?.phase).toBe("preview");
    expect(URL.createObjectURL).toHaveBeenCalledTimes(2);
  });

  it("hands the same lists to every action", async () => {
    const seen: LookupSet[] = [];
    const lists = { categories: productLookups.categories } as LookupSet;
    const load = vi.fn(() => Promise.resolve(lists));
    const entity = makeProductEntity({
      loadLookups: load,
      create: (_payload, ctx) => {
        seen.push(ctx.lookups);
        return Promise.resolve(undefined);
      },
    });
    const exchange = createExchange({
      entity: () => entity,
      filters: () => undefined,
      publish: () => undefined,
      imported: () => undefined,
    });
    await exchange.actions.openFile(await fileWithRows(HEADERS, oneRow));
    await exchange.actions.startImport();
    expect(seen).toEqual([lists]);
  });

  it("gives the load an abort signal", async () => {
    const load = vi.fn((signal: AbortSignal) => Promise.resolve(signal.aborted ? {} : productLookups));
    const { exchange } = setUp(load);
    await exchange.actions.downloadTemplate();
    expect(load.mock.calls[0]?.[0]).toBeInstanceOf(AbortSignal);
    expect(load.mock.calls[0]?.[0].aborted).toBe(false);
  });

  it("makes concurrent actions share the one load that is in flight", async () => {
    const gate = deferred<LookupSet>();
    const load = vi.fn(() => gate.promise);
    const { exchange, latest } = setUp(load);
    const file = await fileWithRows(HEADERS, oneRow);

    const reading = opened(file, exchange);
    await vi.waitFor(() => expect(load).toHaveBeenCalledTimes(1));
    const template = exchange.actions.downloadTemplate();
    const exporting = exchange.actions.exportAll();
    await Promise.resolve();
    expect(load).toHaveBeenCalledTimes(1);

    gate.resolve(productLookups);
    await Promise.all([reading, template, exporting]);
    expect(load).toHaveBeenCalledTimes(1);
    expect(latest()).toMatchObject({ phase: "preview", downloadingTemplate: false, exporting: null });
  });

  it("does not ask for anything when the entity has no lists", async () => {
    const entity = { ...makeProductEntity(), loadLookups: undefined };
    const exchange = createExchange({
      entity: () => entity,
      filters: () => undefined,
      publish: () => undefined,
      imported: () => undefined,
    });
    await expect(exchange.actions.downloadTemplate()).resolves.toBeUndefined();
  });
});

describe("the cache is dropped when it should be", () => {
  it("loads again after reset: a new dialog session starts from the server's lists", async () => {
    const load = vi.fn(() => Promise.resolve(productLookups));
    const { exchange } = setUp(load);
    await exchange.actions.downloadTemplate();
    exchange.actions.reset();
    await exchange.actions.downloadTemplate();
    expect(load).toHaveBeenCalledTimes(2);
  });

  it("loads again after the exchange is disposed and armed again", async () => {
    const load = vi.fn(() => Promise.resolve(productLookups));
    const { exchange } = setUp(load);
    await exchange.actions.downloadTemplate();
    exchange.dispose();
    exchange.attach();
    await exchange.actions.downloadTemplate();
    expect(load).toHaveBeenCalledTimes(2);
  });

  it("aborts the load in flight on dispose, so nothing outlives the dialog", async () => {
    const signals: AbortSignal[] = [];
    const load = vi.fn((signal: AbortSignal) => {
      signals.push(signal);
      return new Promise<LookupSet>(() => undefined);
    });
    const { exchange } = setUp(load);
    void exchange.actions.downloadTemplate();
    await vi.waitFor(() => expect(load).toHaveBeenCalledTimes(1));
    exchange.dispose();
    expect(signals[0]?.aborted).toBe(true);
  });

  it("loads again after an import that created a record: the lists have changed", async () => {
    const load = vi.fn(() => Promise.resolve(productLookups));
    const { exchange } = setUp(load);
    await opened(await fileWithRows(HEADERS, oneRow), exchange);
    await exchange.actions.startImport();
    expect(load).toHaveBeenCalledTimes(1);
    await exchange.actions.downloadTemplate();
    expect(load).toHaveBeenCalledTimes(2);
  });

  it("loads again after a cancelled import that had created a record", async () => {
    const gate = deferred<undefined>();
    const load = vi.fn(() => Promise.resolve(productLookups));
    const { exchange, latest } = setUp(load, () => gate.promise);
    await opened(await fileWithRows(HEADERS, [...oneRow, ["A-2", "Nut"]]), exchange);
    const running = exchange.actions.startImport();
    await vi.waitFor(() => expect(latest()?.phase).toBe("importing"));
    exchange.actions.cancelImport();
    gate.resolve(undefined);
    await running;
    expect(latest()?.summary).toMatchObject({ created: 1, cancelled: true });
    await exchange.actions.downloadTemplate();
    expect(load).toHaveBeenCalledTimes(2);
  });

  it("keeps the lists after an import that created nothing", async () => {
    const load = vi.fn(() => Promise.resolve(productLookups));
    const { exchange, latest } = setUp(load, () => Promise.reject(new ApiError(422, "x", { detail: "Refused" })));
    await opened(await fileWithRows(HEADERS, oneRow), exchange);
    await exchange.actions.startImport();
    expect(latest()?.summary).toMatchObject({ created: 0, failed: 1 });
    await exchange.actions.downloadTemplate();
    expect(load).toHaveBeenCalledTimes(1);
  });

  it("loads again for a different entity: another list of records is another set of lists", async () => {
    const first = vi.fn(() => Promise.resolve(productLookups));
    const second = vi.fn(() => Promise.resolve(productLookups));
    const { exchange, swap } = setUp(first);
    await exchange.actions.downloadTemplate();
    swap(makeProductEntity({ loadLookups: second }));
    await exchange.actions.downloadTemplate();
    await exchange.actions.downloadTemplate();
    expect(first).toHaveBeenCalledTimes(1);
    expect(second).toHaveBeenCalledTimes(1);
  });
});

describe("a load that fails is not kept", () => {
  it("is asked for again by the next action", async () => {
    const load = vi
      .fn<(signal: AbortSignal) => Promise<LookupSet>>()
      .mockRejectedValueOnce(new ApiError(503, "x", { detail: "Try later" }))
      .mockResolvedValue(productLookups);
    const { exchange, latest } = setUp(load);
    const file = await fileWithRows(HEADERS, oneRow);

    await opened(file, exchange);
    expect(latest()).toMatchObject({
      phase: "error",
      error: "The lists this file is checked against could not be loaded. Try later",
    });
    await opened(file, exchange);
    expect(latest()?.phase).toBe("preview");
    expect(load).toHaveBeenCalledTimes(2);
  });

  it("fails every action that was waiting on it, once, and is then retried by the next", async () => {
    const gate = deferred<LookupSet>();
    const load = vi
      .fn<(signal: AbortSignal) => Promise<LookupSet>>()
      .mockReturnValueOnce(gate.promise)
      .mockResolvedValue(productLookups);
    const { exchange, latest } = setUp(load);

    const template = exchange.actions.downloadTemplate();
    await vi.waitFor(() => expect(load).toHaveBeenCalledTimes(1));
    const exporting = exchange.actions.exportAll();
    gate.reject(new ApiError(500, "x", { detail: "Lists are down" }));
    await Promise.all([template, exporting]);
    expect(load).toHaveBeenCalledTimes(1);
    expect(latest()?.notice).toContain("Lists are down");

    await exchange.actions.downloadTemplate();
    expect(load).toHaveBeenCalledTimes(2);
    expect(latest()?.notice).toBeNull();
  });

  it("is retried on the sample sheet and on export too", async () => {
    const load = vi
      .fn<(signal: AbortSignal) => Promise<LookupSet>>()
      .mockRejectedValueOnce(new Error("boom"))
      .mockRejectedValueOnce(new Error("boom"))
      .mockResolvedValue(productLookups);
    const { exchange, latest } = setUp(load);
    await exchange.actions.downloadTemplate();
    expect(latest()?.notice).toBe("The sample sheet could not be created. Try again.");
    await exchange.actions.exportAll();
    expect(latest()?.notice).toBe("The products could not be exported. Try again.");
    await exchange.actions.exportAll();
    expect(latest()?.notice).toBeNull();
    expect(load).toHaveBeenCalledTimes(3);
  });
});

describe("cancelling one action does not cancel the load the others share", () => {
  it("lets a file being read use the load after an export that started it was cancelled", async () => {
    const gate = deferred<LookupSet>();
    const signals: AbortSignal[] = [];
    const load = vi.fn((signal: AbortSignal) => {
      signals.push(signal);
      return gate.promise;
    });
    const { exchange, latest } = setUp(load);

    const exporting = exchange.actions.exportAll();
    await vi.waitFor(() => expect(load).toHaveBeenCalledTimes(1));
    const reading = opened(await fileWithRows(HEADERS, oneRow), exchange);
    exchange.actions.cancelExport();
    await exporting;
    expect(signals[0]?.aborted).toBe(false);
    expect(latest()?.exporting).toBeNull();

    gate.resolve(productLookups);
    await reading;
    expect(load).toHaveBeenCalledTimes(1);
    expect(latest()?.phase).toBe("preview");
  });
});
