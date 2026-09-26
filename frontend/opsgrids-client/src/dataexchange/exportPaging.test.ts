import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../http";
import { createExchange } from "./exchange";
import { acquirePageSlot, resetPageBudgetForTests } from "./lookups";
import { makeProductEntity } from "./testkit";
import type { ProductFilters, ProductItem } from "./testkit";
import type { DataExchangeState, ExportPage } from "./types";

/*
 * An export pages the list 200 rows at a time. Those requests come out of the same per-minute
 * budget as the reference lists (and everything else the user does), and a 429 is waited out
 * rather than failing a long export half way. Time is faked: the waits are a minute long.
 */

const BUDGET = 150;
const MINUTE = 60_000;

let downloads: number;

beforeEach(() => {
  downloads = 0;
  resetPageBudgetForTests();
  URL.createObjectURL = vi.fn(() => "blob:test");
  URL.revokeObjectURL = vi.fn();
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {
    downloads += 1;
  });
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
});

afterEach(() => {
  vi.useRealTimers();
  resetPageBudgetForTests();
});

function item(i: number): ProductItem {
  return {
    id: `p-${i}`,
    sku: `SKU-${i}`,
    name: `Product ${i}`,
    status: "in_stock",
    category: "Hardware",
    priceMinor: 100,
    currency: "INR",
    quantity: i,
    active: true,
    email: "orders@example.com",
    createdAt: "2026-09-26T10:30:00Z",
  };
}

type Args = { page: number; size: number; filters: ProductFilters; signal: AbortSignal };

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

function setUp(fetchPage: (args: Args) => Promise<ExportPage<ProductItem>>) {
  const states: DataExchangeState[] = [];
  const entity = makeProductEntity({ fetchPage });
  const exchange = createExchange({
    entity: () => entity,
    filters: () => ({ search: "" }),
    publish: (state) => states.push(state),
    imported: () => undefined,
  });
  return { exchange, latest: () => states.at(-1) };
}

/** Uses up all but `left` of the shared budget. */
async function spendBudget(left: number): Promise<void> {
  for (let i = 0; i < BUDGET - left; i += 1) await acquirePageSlot();
}

describe("export paging and the shared request budget", () => {
  it("waits for room in the budget instead of spending what the rest of the page needs", async () => {
    await spendBudget(0);
    const fetchPage = vi.fn((args: Args) => pagesOf(150)(args));
    const { exchange, latest } = setUp(fetchPage);

    const exporting = exchange.actions.exportAll();
    await vi.advanceTimersByTimeAsync(MINUTE - 1000);
    expect(fetchPage).not.toHaveBeenCalled();
    expect(latest()?.exporting).toEqual({ fetched: 0, total: 0 });

    await vi.advanceTimersByTimeAsync(1000);
    expect(fetchPage).toHaveBeenCalledTimes(1);
    vi.useRealTimers();
    await exporting;
    expect(downloads).toBe(1);
    expect(latest()).toMatchObject({ exporting: null, notice: null });
  });

  it("counts each page it fetches against the budget", async () => {
    // Two slots left, three pages to fetch: the third has to wait for the window to move on.
    await spendBudget(2);
    const fetchPage = vi.fn((args: Args) => pagesOf(450)(args));
    const { exchange } = setUp(fetchPage);

    const exporting = exchange.actions.exportAll();
    await vi.advanceTimersByTimeAsync(1000);
    expect(fetchPage).toHaveBeenCalledTimes(2);
    expect(fetchPage.mock.calls.map(([args]) => args.page)).toEqual([1, 2]);

    await vi.advanceTimersByTimeAsync(MINUTE);
    expect(fetchPage.mock.calls.map(([args]) => args.page)).toEqual([1, 2, 3]);
    vi.useRealTimers();
    await exporting;
    expect(downloads).toBe(1);
  });

  it("does not wait at all when the budget has room", async () => {
    const fetchPage = vi.fn((args: Args) => pagesOf(450)(args));
    const { exchange } = setUp(fetchPage);
    const exporting = exchange.actions.exportAll();
    await vi.advanceTimersByTimeAsync(0);
    await vi.waitFor(() => expect(fetchPage).toHaveBeenCalledTimes(3));
    vi.useRealTimers();
    await exporting;
    expect(downloads).toBe(1);
  });

  it("waits out a 429 for a minute and asks for the same page again", async () => {
    let calls = 0;
    const fetchPage = vi.fn((args: Args) => {
      calls += 1;
      return calls === 1
        ? Promise.reject(new ApiError(429, "x", { detail: "Too many requests" }))
        : pagesOf(50)(args);
    });
    const { exchange, latest } = setUp(fetchPage);

    const exporting = exchange.actions.exportAll();
    await vi.advanceTimersByTimeAsync(MINUTE - 1000);
    expect(fetchPage).toHaveBeenCalledTimes(1);
    expect(latest()?.notice).toBeNull();

    await vi.advanceTimersByTimeAsync(1000);
    expect(fetchPage).toHaveBeenCalledTimes(2);
    expect(fetchPage.mock.calls.map(([args]) => args.page)).toEqual([1, 1]);
    vi.useRealTimers();
    await exporting;
    expect(downloads).toBe(1);
    expect(latest()).toMatchObject({ exporting: null, notice: null });
  });

  it("gives up after two waits and reports the server's sentence, with no file", async () => {
    const fetchPage = vi.fn(() =>
      Promise.reject<ExportPage<ProductItem>>(new ApiError(429, "x", { detail: "Too many requests" })),
    );
    const { exchange, latest } = setUp(fetchPage);

    const exporting = exchange.actions.exportAll();
    await vi.advanceTimersByTimeAsync(MINUTE * 3);
    vi.useRealTimers();
    await exporting;
    expect(fetchPage).toHaveBeenCalledTimes(3);
    expect(latest()).toMatchObject({
      exporting: null,
      notice: "The products could not be exported. Too many requests",
    });
    expect(downloads).toBe(0);
  });

  it("never retries a status that is not a 429", async () => {
    const fetchPage = vi.fn(() =>
      Promise.reject<ExportPage<ProductItem>>(new ApiError(403, "x", { detail: "You may not read products" })),
    );
    const { exchange, latest } = setUp(fetchPage);
    await exchange.actions.exportAll();
    expect(fetchPage).toHaveBeenCalledTimes(1);
    expect(latest()?.notice).toBe("The products could not be exported. You may not read products");
  });

  it("stops quietly when it is cancelled during the wait: no more requests, no file, no notice", async () => {
    const fetchPage = vi.fn(() =>
      Promise.reject<ExportPage<ProductItem>>(new ApiError(429, "x", { detail: "Too many requests" })),
    );
    const { exchange, latest } = setUp(fetchPage);

    const exporting = exchange.actions.exportAll();
    await vi.advanceTimersByTimeAsync(1000);
    expect(fetchPage).toHaveBeenCalledTimes(1);

    exchange.actions.cancelExport();
    expect(latest()?.exporting).toBeNull();
    await vi.advanceTimersByTimeAsync(MINUTE * 5);
    vi.useRealTimers();
    await exporting;
    expect(fetchPage).toHaveBeenCalledTimes(1);
    expect(downloads).toBe(0);
    expect(latest()).toMatchObject({ exporting: null, notice: null });
  });

  it("stops quietly when it is cancelled while waiting for room in the budget", async () => {
    await spendBudget(0);
    const fetchPage = vi.fn((args: Args) => pagesOf(10)(args));
    const { exchange, latest } = setUp(fetchPage);

    const exporting = exchange.actions.exportAll();
    await vi.advanceTimersByTimeAsync(1000);
    exchange.actions.cancelExport();
    await vi.advanceTimersByTimeAsync(MINUTE * 2);
    vi.useRealTimers();
    await exporting;
    expect(fetchPage).not.toHaveBeenCalled();
    expect(downloads).toBe(0);
    expect(latest()).toMatchObject({ exporting: null, notice: null });
  });

  it("is told to stop through the signal it passes to the page request", async () => {
    const signals: AbortSignal[] = [];
    const fetchPage = vi.fn((args: Args) => {
      signals.push(args.signal);
      return new Promise<ExportPage<ProductItem>>(() => undefined);
    });
    const { exchange } = setUp(fetchPage);
    void exchange.actions.exportAll();
    await vi.advanceTimersByTimeAsync(0);
    await vi.waitFor(() => expect(fetchPage).toHaveBeenCalledTimes(1));
    exchange.actions.cancelExport();
    expect(signals[0]?.aborted).toBe(true);
  });
});
