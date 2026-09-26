import { describe, expect, it, vi } from "vitest";

import { ApiError } from "../http";
import { runImport } from "./runner";
import type { RunImportArgs, RunnerRow } from "./runner";
import { makeProductEntity, productLookups } from "./testkit";
import type { ProductPayload } from "./testkit";
import type { ImportProgress } from "./types";
import { RowProblem } from "./types";

const ctx = { lookups: productLookups, signal: new AbortController().signal };

function rowsOf(count: number): RunnerRow[] {
  return Array.from({ length: count }, (_, i) => ({
    row: i + 2,
    values: { sku: `SKU-${i + 1}`, name: `Product ${i + 1}` },
    label: `SKU-${i + 1}`,
  }));
}

/** A clock and a sleep that share time: sleeping advances it, and nothing waits for real. */
function fakeTime() {
  let elapsed = 0;
  const sleep = vi.fn((ms: number): Promise<void> => {
    elapsed += ms;
    return Promise.resolve();
  });
  return {
    sleep,
    now: () => elapsed,
    pass: (ms: number) => {
      elapsed += ms;
    },
  };
}

function problem(status: number, detail: string, errors?: { field: string; code: string; message: string }[]): ApiError {
  return new ApiError(status, detail, { status, detail, ...(errors ? { errors } : {}) });
}

type Create = (payload: ProductPayload) => Promise<{ id?: string } | undefined>;

interface Harness {
  run: () => ReturnType<typeof runImport>;
  progress: ImportProgress[];
  time: ReturnType<typeof fakeTime>;
  create: ReturnType<typeof vi.fn<Create>>;
  /** The clock reading at each request start. */
  starts: number[];
}

function harness(
  create: Create,
  rows: RunnerRow[],
  extra: Partial<RunImportArgs> = {},
  time = fakeTime(),
): Harness {
  const starts: number[] = [];
  const spy = vi.fn<Create>((payload) => {
    starts.push(time.now());
    return create(payload);
  });
  const entity = makeProductEntity({ create: (payload) => spy(payload) });
  const progress: ImportProgress[] = [];
  return {
    time,
    progress,
    starts,
    create: spy,
    run: () =>
      runImport({
        entity,
        rows,
        ctx,
        onProgress: (p) => progress.push(p),
        isCancelled: () => false,
        sleep: time.sleep,
        now: time.now,
        ...extra,
      }),
  };
}

describe("runImport: the ordinary run", () => {
  it("creates every row in order, one at a time, and reports each outcome with its sheet row", async () => {
    let active = 0;
    let overlapped = false;
    const order: string[] = [];
    const h = harness(async (payload) => {
      active += 1;
      overlapped ||= active > 1;
      order.push(payload.sku);
      await Promise.resolve();
      active -= 1;
      return { id: `id-${payload.sku}` };
    }, rowsOf(5));
    const result = await h.run();
    expect(order).toEqual(["SKU-1", "SKU-2", "SKU-3", "SKU-4", "SKU-5"]);
    expect(overlapped).toBe(false);
    expect(result).toMatchObject({ created: 5, skipped: 0, failed: 0, invalid: 0, cancelled: false });
    expect(result.outcomes.map((o) => [o.row, o.status, o.id, o.label])).toEqual([
      [2, "created", "id-SKU-1", "SKU-1"],
      [3, "created", "id-SKU-2", "SKU-2"],
      [4, "created", "id-SKU-3", "SKU-3"],
      [5, "created", "id-SKU-4", "SKU-4"],
      [6, "created", "id-SKU-5", "SKU-5"],
    ]);
  });

  it("sends the payload the entity builds from the row, with the run's context", async () => {
    const seen: unknown[] = [];
    const entity = makeProductEntity({
      create: (payload, given) => {
        seen.push([payload, given]);
        return Promise.resolve(undefined);
      },
    });
    await runImport({
      entity,
      rows: [{ row: 2, values: { sku: "A", name: "B", price: 100 }, label: "A" }],
      ctx,
      onProgress: () => undefined,
      isCancelled: () => false,
      sleep: () => Promise.resolve(),
      now: () => 0,
    });
    expect(seen).toEqual([[expect.objectContaining({ sku: "A", name: "B", priceMinor: 100 }), ctx]]);
  });

  it("accepts a create that returns nothing", async () => {
    const h = harness(() => Promise.resolve(undefined), rowsOf(1));
    const result = await h.run();
    expect(result.outcomes[0]).toMatchObject({ status: "created", id: undefined });
  });

  it("reports progress from zero to done, with counts that add up", async () => {
    const h = harness((payload) => {
      if (payload.sku === "SKU-2") return Promise.reject(problem(409, "That SKU already exists"));
      if (payload.sku === "SKU-3") return Promise.reject(problem(422, "Name is too long"));
      return Promise.resolve({ id: payload.sku });
    }, rowsOf(3));
    await h.run();
    expect(h.progress[0]).toEqual({ done: 0, total: 3, created: 0, skipped: 0, failed: 0, waiting: null });
    expect(h.progress.at(-1)).toEqual({ done: 3, total: 3, created: 1, skipped: 1, failed: 1, waiting: null });
    expect(h.progress.map((p) => p.done)).toEqual([0, 1, 2, 3]);
  });

  it("is an empty result for no rows", async () => {
    const h = harness(() => Promise.resolve(undefined), []);
    expect(await h.run()).toEqual({ outcomes: [], created: 0, skipped: 0, failed: 0, invalid: 0, cancelled: false });
  });
});

describe("runImport: a row that already exists (409)", () => {
  it("is skipped with the server's sentence, and the run goes on", async () => {
    const h = harness((payload) => {
      if (payload.sku === "SKU-1") return Promise.reject(problem(409, "A product with SKU SKU-1 already exists"));
      return Promise.resolve({ id: payload.sku });
    }, rowsOf(2));
    const result = await h.run();
    expect(result.outcomes[0]).toMatchObject({
      row: 2,
      status: "skipped",
      message: "A product with SKU SKU-1 already exists",
    });
    expect(result.outcomes[1]?.status).toBe("created");
    expect(h.create).toHaveBeenCalledTimes(2);
    expect(result).toMatchObject({ created: 1, skipped: 1, failed: 0 });
  });
});

describe("runImport: a 409 is a skip only when the record already exists", () => {
  async function runOne(error: ApiError) {
    const h = harness(() => Promise.reject(error), rowsOf(1));
    const result = await h.run();
    return { result, outcome: result.outcomes[0], calls: h.create.mock.calls.length };
  }

  it.each([
    "A supplier coded 'S-1' already exists",
    "That SKU was already recorded",
    "This conflicts with something that already exists",
    "Company ALREADY EXISTS in this organisation",
  ])("skips on the sentence %j", async (detail) => {
    const { outcome, result } = await runOne(problem(409, detail));
    expect(outcome).toMatchObject({ status: "skipped", message: detail });
    expect(result).toMatchObject({ skipped: 1, failed: 0 });
  });

  it("skips on the duplicate field code, whatever the sentence says", async () => {
    const error = problem(409, "The record could not be saved", [
      { field: "code", code: "duplicate", message: "Code is taken" },
    ]);
    const { outcome } = await runOne(error);
    expect(outcome?.status).toBe("skipped");
  });

  it("skips when only the problem's title says it", async () => {
    const titled = new ApiError(409, "x", { title: "Company already exists" });
    expect((await runOne(titled)).outcome?.status).toBe("skipped");
  });

  it.each([
    "The warehouse has no default bin",
    "Unknown pipeline stage 'Almost won'",
    "Cannot receive stock while a stocktake is open",
    "The version you are editing is out of date",
  ])("fails, with the server's sentence, on the other conflict %j", async (detail) => {
    const { outcome, result, calls } = await runOne(problem(409, detail));
    expect(outcome).toMatchObject({ status: "failed", message: detail });
    expect(result).toMatchObject({ skipped: 0, failed: 1, created: 0 });
    expect(calls).toBe(1);
  });

  it("fails a 409 whose only field code is something else, and one with no body at all", async () => {
    const coded = problem(409, "Stage is unknown", [{ field: "stage", code: "unknown_stage", message: "Stage is unknown" }]);
    expect((await runOne(coded)).outcome?.status).toBe("failed");
    const bare = await runOne(new ApiError(409, "Request failed with 409", null));
    expect(bare.outcome).toMatchObject({ status: "failed", message: "Request failed with 409" });
  });

  it("does not mistake a word that merely contains 'already' for the sentence", async () => {
    expect((await runOne(problem(409, "Unalready configured"))).outcome?.status).toBe("failed");
  });

  it("counts a refused conflict as failed in the progress the dialog shows, and goes on", async () => {
    const h = harness((payload) => {
      if (payload.sku === "SKU-1") return Promise.reject(problem(409, "The warehouse has no default bin"));
      return Promise.resolve({ id: payload.sku });
    }, rowsOf(2));
    const result = await h.run();
    expect(h.progress.at(-1)).toMatchObject({ done: 2, created: 1, skipped: 0, failed: 1 });
    expect(result.outcomes.map((o) => o.status)).toEqual(["failed", "created"]);
  });
});

describe("runImport: the rate limit (429)", () => {
  it("waits out the window counting down each second, then retries the same row and carries on", async () => {
    let calls = 0;
    const h = harness((payload) => {
      calls += 1;
      if (calls === 2) return Promise.reject(problem(429, "Too many requests"));
      return Promise.resolve({ id: payload.sku });
    }, rowsOf(3), { backoffMs: 3000 });
    const result = await h.run();

    expect(result).toMatchObject({ created: 3, failed: 0, skipped: 0 });
    expect(h.create.mock.calls.map(([payload]) => payload.sku)).toEqual(["SKU-1", "SKU-2", "SKU-2", "SKU-3"]);

    const waits = h.progress.filter((p) => p.waiting !== null).map((p) => p.waiting);
    expect(waits).toEqual([
      { reason: "rate-limit", seconds: 3 },
      { reason: "rate-limit", seconds: 2 },
      { reason: "rate-limit", seconds: 1 },
    ]);
    expect(h.progress.at(-1)?.waiting).toBeNull();
    // Nothing is sent while waiting: the counts only move when a row concludes.
    const duringWait = h.progress.filter((p) => p.waiting !== null);
    expect(duringWait.every((p) => p.done === 1)).toBe(true);
  });

  it("waits the full minute by default, in one-second steps", async () => {
    let calls = 0;
    const h = harness(() => {
      calls += 1;
      return calls === 1 ? Promise.reject(problem(429, "Slow down")) : Promise.resolve({ id: "x" });
    }, rowsOf(1));
    await h.run();
    const steps = h.time.sleep.mock.calls.map(([ms]) => ms).filter((ms) => ms === 1000);
    expect(steps).toHaveLength(60);
    expect(h.progress.filter((p) => p.waiting !== null)[0]?.waiting?.seconds).toBe(60);
  });

  it("gives up on a row after three retries and reports the server's sentence, then moves on", async () => {
    const h = harness((payload) => {
      if (payload.sku === "SKU-1") return Promise.reject(problem(429, "Rate limit exceeded. Try again in a minute."));
      return Promise.resolve({ id: payload.sku });
    }, rowsOf(2), { backoffMs: 1000 });
    const result = await h.run();
    expect(h.create.mock.calls.filter(([payload]) => payload.sku === "SKU-1")).toHaveLength(4);
    expect(result.outcomes[0]).toMatchObject({
      status: "failed",
      message: "Rate limit exceeded. Try again in a minute.",
    });
    expect(result.outcomes[1]?.status).toBe("created");
    expect(result).toMatchObject({ created: 1, failed: 1 });
  });

  it("honours a smaller retry budget", async () => {
    const h = harness(() => Promise.reject(problem(429, "Slow")), rowsOf(1), { backoffMs: 1000, maxRetries: 1 });
    await h.run();
    expect(h.create).toHaveBeenCalledTimes(2);
  });

  it("can be cancelled while waiting: the row is not retried and is not reported", async () => {
    let cancelled = false;
    const h = harness(() => Promise.reject(problem(429, "Slow")), rowsOf(3), {
      backoffMs: 5000,
      isCancelled: () => cancelled,
    });
    h.time.sleep.mockImplementation((ms: number) => {
      if (ms === 1000) cancelled = true;
      return Promise.resolve();
    });
    const result = await h.run();
    expect(result.cancelled).toBe(true);
    expect(result.outcomes).toEqual([]);
    expect(h.create).toHaveBeenCalledTimes(1);
  });
});

describe("runImport: everything else fails once", () => {
  it.each([
    [400, "Bad request"],
    [401, "Not signed in"],
    [403, "You may not assign that owner"],
    [404, "Warehouse not found"],
    [422, "Validation failed"],
    [500, "Internal error"],
    [502, "Bad gateway"],
    [503, "Service unavailable"],
  ])("a %i is sent exactly once and reports the server's sentence", async (status, detail) => {
    const h = harness((payload) => {
      if (payload.sku === "SKU-1") return Promise.reject(problem(status, detail));
      return Promise.resolve({ id: payload.sku });
    }, rowsOf(2));
    const result = await h.run();
    expect(h.create.mock.calls.filter(([payload]) => payload.sku === "SKU-1")).toHaveLength(1);
    expect(result.outcomes[0]).toMatchObject({ status: "failed", message: detail });
    expect(result.outcomes[1]?.status).toBe("created");
    // Never a rate-limit wait for anything but a 429.
    expect(h.progress.every((p) => p.waiting === null)).toBe(true);
  });

  it("adds the server's field errors to the sentence", async () => {
    const h = harness(
      () =>
        Promise.reject(
          problem(422, "Request validation failed", [
            { field: "name", code: "string_too_short", message: "Name is too short" },
            { field: "price.currency", code: "enum", message: "Unsupported currency" },
          ]),
        ),
      rowsOf(1),
    );
    const result = await h.run();
    expect(result.outcomes[0]?.message).toBe(
      "Request validation failed (name: Name is too short; price.currency: Unsupported currency)",
    );
  });

  it("fails a dropped connection once, with a generic sentence rather than the machinery's", async () => {
    const h = harness(() => Promise.reject(new TypeError("Failed to fetch")), rowsOf(2));
    const result = await h.run();
    expect(h.create).toHaveBeenCalledTimes(2);
    expect(result.outcomes.map((o) => o.status)).toEqual(["failed", "failed"]);
    expect(result.outcomes[0]?.message).toBe("The request could not be completed. Check your connection and try again.");
    expect(result.outcomes[0]?.message).not.toContain("fetch");
  });
});

describe("runImport: rows the entity refuses", () => {
  it("marks a row whose payload cannot be built as invalid and never sends it", async () => {
    const h = harness((payload) => Promise.resolve({ id: payload.sku }), [
      { row: 2, values: { sku: "A", name: "B", category: "Nowhere" }, label: "A" },
      ...rowsOf(1).map((r) => ({ ...r, row: 3 })),
    ]);
    const result = await h.run();
    expect(h.create).toHaveBeenCalledTimes(1);
    expect(result.outcomes[0]).toMatchObject({ row: 2, status: "invalid", message: 'Category "Nowhere" was not found' });
    expect(result.invalid).toBe(1);
    // It did not spend a request, so it did not spend a throttle interval either.
    expect(h.time.sleep).not.toHaveBeenCalled();
    expect(h.progress.at(-1)).toMatchObject({ done: 2, created: 1, failed: 1 });
  });

  it("gives a generic sentence when toPayload throws something that is not a RowProblem", async () => {
    const entity = makeProductEntity();
    const broken = {
      ...entity,
      toPayload: () => {
        throw new TypeError("undefined is not a function");
      },
    };
    const result = await runImport({
      entity: broken,
      rows: rowsOf(1),
      ctx,
      onProgress: () => undefined,
      isCancelled: () => false,
      sleep: () => Promise.resolve(),
      now: () => 0,
    });
    expect(result.outcomes[0]).toMatchObject({ status: "invalid", message: "This row could not be prepared for import." });
  });

  it("marks a row invalid when the create itself throws a RowProblem", async () => {
    const h = harness(() => Promise.reject(new RowProblem("That warehouse is closed")), rowsOf(1));
    const result = await h.run();
    expect(result.outcomes[0]).toMatchObject({ status: "invalid", message: "That warehouse is closed" });
  });
});

describe("runImport: cancelling", () => {
  it("stops after the row in flight and leaves the rest out of the outcomes", async () => {
    let cancelled = false;
    const h = harness((payload) => {
      if (payload.sku === "SKU-2") cancelled = true;
      return Promise.resolve({ id: payload.sku });
    }, rowsOf(6), { isCancelled: () => cancelled });
    const result = await h.run();
    expect(result.cancelled).toBe(true);
    expect(h.create).toHaveBeenCalledTimes(2);
    expect(result.outcomes.map((o) => o.row)).toEqual([2, 3]);
    expect(result.created).toBe(2);
  });

  it("does not start at all when cancelled before the first row", async () => {
    const h = harness(() => Promise.resolve(undefined), rowsOf(3), { isCancelled: () => true });
    const result = await h.run();
    expect(h.create).not.toHaveBeenCalled();
    expect(result).toMatchObject({ cancelled: true, outcomes: [] });
  });

  it("does not report an aborted request as a failure of that row", async () => {
    let cancelled = false;
    const h = harness(() => {
      cancelled = true;
      const abort = new Error("The operation was aborted");
      abort.name = "AbortError";
      return Promise.reject(abort);
    }, rowsOf(3), { isCancelled: () => cancelled });
    const result = await h.run();
    expect(result.cancelled).toBe(true);
    expect(result.outcomes).toEqual([]);
    expect(h.create).toHaveBeenCalledTimes(1);
  });

  it("does not send the next row when cancelled during the throttle wait", async () => {
    let cancelled = false;
    const h = harness(() => Promise.resolve({ id: "x" }), rowsOf(3), { isCancelled: () => cancelled });
    h.time.sleep.mockImplementation(() => {
      cancelled = true;
      return Promise.resolve();
    });
    const result = await h.run();
    expect(h.create).toHaveBeenCalledTimes(1);
    expect(result.outcomes).toHaveLength(1);
    expect(result.cancelled).toBe(true);
  });
});

describe("runImport: the throttle", () => {
  it("keeps at least 350 ms between the starts of two requests", async () => {
    const h = harness(() => Promise.resolve({ id: "x" }), rowsOf(4));
    await h.run();
    expect(h.starts).toEqual([0, 350, 700, 1050]);
    expect(h.time.sleep.mock.calls.map(([ms]) => ms)).toEqual([350, 350, 350]);
  });

  it("only waits for what is left of the interval when a request was itself slow", async () => {
    const time = fakeTime();
    let call = 0;
    const h = harness(
      () => {
        call += 1;
        time.pass(call === 1 ? 200 : 500);
        return Promise.resolve({ id: "x" });
      },
      rowsOf(3),
      {},
      time,
    );
    await h.run();
    // The first took 200 ms, so 150 more; the second took 500 ms, so no wait at all.
    expect(time.sleep.mock.calls.map(([ms]) => ms)).toEqual([150]);
    expect(h.starts).toEqual([0, 350, 850]);
  });

  it("honours a different interval", async () => {
    const h = harness(() => Promise.resolve({ id: "x" }), rowsOf(3), { minIntervalMs: 1000 });
    await h.run();
    expect(h.time.sleep.mock.calls.map(([ms]) => ms)).toEqual([1000, 1000]);
  });

  it("spaces a retry from the failed attempt as well", async () => {
    let calls = 0;
    const h = harness(
      () => {
        calls += 1;
        return calls === 1 ? Promise.reject(problem(429, "Slow")) : Promise.resolve({ id: "x" });
      },
      rowsOf(1),
      { backoffMs: 100 },
    );
    await h.run();
    // A 100 ms window is shorter than the interval, so the retry waits the other 250.
    expect(h.starts).toEqual([0, 350]);
    expect(h.time.sleep.mock.calls.map(([ms]) => ms)).toEqual([100, 250]);
  });
});
