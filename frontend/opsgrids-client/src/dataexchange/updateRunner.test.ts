import { describe, expect, it, vi } from "vitest";

import { ApiError } from "../http";
import { prepareImport } from "./preview";
import { PARTLY_SAVED_MESSAGE, STOPPED_PARTWAY_MESSAGE, runImport } from "./runner";
import type { RunImportArgs, RunnerRow } from "./runner";
import { fileWithRows, makeProductEntity, makeProductUpdate, productLookups, storedProduct } from "./testkit";
import type { ProductItem, ProductPayload } from "./testkit";
import type { ExtraStep, ImportMode, ImportProgress } from "./types";
import { readWorkbook } from "./workbook";

const ctx = { lookups: productLookups, signal: new AbortController().signal };

const HEADERS = ["Id", "SKU *", "Name *", "Price"];
type Cell = string | number | null;

const stored: ProductItem[] = [storedProduct(1), storedProduct(2), storedProduct(3)];

function problem(status: number, detail: string): ApiError {
  return new ApiError(status, detail, { status, detail });
}

function fakeTime() {
  let elapsed = 0;
  const sleep = vi.fn((ms: number): Promise<void> => {
    elapsed += ms;
    return Promise.resolve();
  });
  return { sleep, now: () => elapsed };
}

type Send = (existing: ProductItem, body: Readonly<Record<string, unknown>>) => Promise<void>;
type Create = (payload: ProductPayload) => Promise<{ id?: string } | undefined>;

interface Options {
  send?: Send;
  create?: Create;
  extras?: (calls: string[]) => ExtraStep[];
  mode?: ImportMode;
  run?: Partial<RunImportArgs>;
}

/** Reads `rows` the way a file would be, prepares them in update mode, and runs the ones that can be sent. */
async function run(rows: readonly (readonly Cell[])[], options: Options = {}) {
  const time = fakeTime();
  const calls: string[] = [];
  const starts: number[] = [];
  const send = vi.fn<Send>((existing, body) => {
    starts.push(time.now());
    calls.push(`patch ${existing.id} ${JSON.stringify(body)}`);
    return options.send === undefined ? Promise.resolve() : options.send(existing, body);
  });
  const create = vi.fn<Create>((payload) => {
    starts.push(time.now());
    calls.push(`create ${payload.sku}`);
    return options.create === undefined ? Promise.resolve({ id: "new-id" }) : options.create(payload);
  });
  const entity = makeProductEntity({
    recordIdKey: "id",
    create: (payload) => create(payload),
    update: makeProductUpdate({
      items: stored,
      send: (existing, body) => send(existing, body),
      ...(options.extras === undefined ? {} : { extras: () => options.extras?.(calls) ?? [] }),
    }),
  });
  const sheet = await readWorkbook(await fileWithRows(HEADERS, rows), entity);
  const { prepared } = prepareImport(entity, sheet, ctx, "f.xlsx", "addAndUpdate", stored);
  const progress: ImportProgress[] = [];
  const result = await runImport({
    entity,
    rows: prepared.valid,
    ctx,
    mode: options.mode ?? "addAndUpdate",
    onProgress: (p) => progress.push(p),
    isCancelled: () => false,
    sleep: time.sleep,
    now: time.now,
    ...options.run,
  });
  return { result, calls, send, create, starts, progress, time, rows: prepared.valid as RunnerRow[] };
}

const rename = ["p-1", null, "Bracket", null] as const;

describe("runImport: rows that update a record", () => {
  it("sends one update with only the changed fields, and reports the row as updated with what changed", async () => {
    const { result, calls, create } = await run([["p-1", null, "Bracket", "12.50"]]);
    expect(calls).toEqual(['patch p-1 {"name":"Bracket","price_minor":1250}']);
    expect(create).not.toHaveBeenCalled();
    expect(result).toMatchObject({ created: 0, updated: 1, failed: 0, cancelled: false });
    expect(result.outcomes).toEqual([
      { row: 2, status: "updated", message: null, id: undefined, changes: ["Name", "Price"], label: "SKU-1" },
    ]);
  });

  it("creates and updates in the file's order, each the way it should be", async () => {
    const { result, calls } = await run([
      [null, "NEW-1", "Washer", null],
      ["p-2", null, "Nut", null],
      [null, "NEW-2", "Bolt", null],
    ]);
    expect(calls).toEqual(["create NEW-1", 'patch p-2 {"name":"Nut"}', "create NEW-2"]);
    expect(result).toMatchObject({ created: 2, updated: 1 });
    expect(result.outcomes.map((o) => [o.row, o.status])).toEqual([
      [2, "created"],
      [3, "updated"],
      [4, "created"],
    ]);
  });

  it("makes no request at all for a row that changes nothing", async () => {
    const { rows, calls, result } = await run([
      ["p-1", "SKU-1", "Product 1", "10"],
      ["p-2", null, null, null],
    ]);
    expect(rows).toEqual([]);
    expect(calls).toEqual([]);
    expect(result.outcomes).toEqual([]);
  });

  it("counts an update row that turns out to change nothing when its turn comes as unchanged, without a request", async () => {
    const { rows, result, send } = await run([["p-1", null, "Bracket", null]]);
    expect(result.updated).toBe(1);
    send.mockClear();
    const row = rows[0];
    if (row?.update === undefined) throw new Error("expected an update row");
    const settled: RunnerRow = { ...row, update: { plan: () => null } };
    const again = await runImport({
      entity: makeProductEntity({ update: makeProductUpdate({ items: stored }) }),
      rows: [settled],
      ctx,
      mode: "addAndUpdate",
      onProgress: () => undefined,
      isCancelled: () => false,
      sleep: () => Promise.resolve(),
      now: () => 0,
    });
    expect(again).toMatchObject({ unchanged: 1, updated: 0 });
    expect(again.outcomes[0]?.status).toBe("unchanged");
    expect(send).not.toHaveBeenCalled();
  });

  it("reports progress with the updated count, and the counts add up", async () => {
    const { progress } = await run([
      ["p-1", null, "A", null],
      [null, "NEW-1", "B", null],
    ]);
    expect(progress[0]).toEqual({ done: 0, total: 2, created: 0, updated: 0, skipped: 0, failed: 0, waiting: null });
    expect(progress.at(-1)).toEqual({ done: 2, total: 2, created: 1, updated: 1, skipped: 0, failed: 0, waiting: null });
  });

  it("spaces every request, not only every row: an update with two extra steps is three requests", async () => {
    const steps = (calls: string[]): ExtraStep[] => [
      { changed: ["Stage"], run: () => Promise.resolve(void calls.push("stage")) },
      { changed: ["Phone"], run: () => Promise.resolve(void calls.push("phone")) },
    ];
    const { starts, calls } = await run(
      [
        ["p-1", null, "A", null],
        ["p-2", null, "B", null],
      ],
      { extras: steps },
    );
    expect(calls.map((call) => call.split(" ")[0])).toEqual(["patch", "stage", "phone", "patch", "stage", "phone"]);
    // Only the field update records its start; it is one request in every three, so at least 3 * 350 ms apart.
    expect(starts).toHaveLength(2);
    expect((starts[1] ?? 0) - (starts[0] ?? 0)).toBeGreaterThanOrEqual(3 * 350);
  });

  it("sends the extra steps after the field update, and counts their columns as changes", async () => {
    const steps = (calls: string[]): ExtraStep[] => [{ changed: ["Stage"], run: () => Promise.resolve(void calls.push("stage")) }];
    const { calls, result } = await run([["p-1", null, "A", null]], { extras: steps });
    expect(calls).toEqual(['patch p-1 {"name":"A"}', "stage"]);
    expect(result.outcomes[0]?.changes).toEqual(["Name", "Stage"]);
  });
});

describe("runImport: a rate limit while updating", () => {
  it("waits out a 429 on the field update and sends the same update again, before any extra step", async () => {
    let attempts = 0;
    const steps = (calls: string[]): ExtraStep[] => [{ changed: ["Stage"], run: () => Promise.resolve(void calls.push("stage")) }];
    const { calls, result, time } = await run([["p-1", null, "A", null]], {
      extras: steps,
      send: () => {
        attempts += 1;
        return attempts === 1 ? Promise.reject(problem(429, "Too many requests")) : Promise.resolve();
      },
    });
    expect(calls).toEqual(['patch p-1 {"name":"A"}', 'patch p-1 {"name":"A"}', "stage"]);
    expect(result).toMatchObject({ updated: 1, failed: 0 });
    expect(time.sleep).toHaveBeenCalledWith(1000);
  });

  it("retries only the extra step that got the 429: the field update, which already went, is not sent again", async () => {
    let attempts = 0;
    const steps = (calls: string[]): ExtraStep[] => [
      {
        changed: ["Stage"],
        run: () => {
          attempts += 1;
          calls.push(`stage#${attempts}`);
          return attempts === 1 ? Promise.reject(problem(429, "Too many requests")) : Promise.resolve();
        },
      },
    ];
    const { calls, result, send } = await run([["p-1", null, "A", null]], { extras: steps });
    expect(send).toHaveBeenCalledTimes(1);
    expect(calls).toEqual(['patch p-1 {"name":"A"}', "stage#1", "stage#2"]);
    expect(result).toMatchObject({ updated: 1, failed: 0 });
  });

  it("gives up after the retries and fails the row with the server's sentence", async () => {
    const { result, send } = await run([["p-1", null, "A", null]], {
      send: () => Promise.reject(problem(429, "Slow down")),
      run: { maxRetries: 2 },
    });
    expect(send).toHaveBeenCalledTimes(3);
    expect(result.outcomes[0]).toMatchObject({ status: "failed", message: "Slow down" });
  });

  it("can be cancelled while waiting: the row is not reported", async () => {
    let cancel = false;
    const { result } = await run([["p-1", null, "A", null]], {
      send: () => Promise.reject(problem(429, "Slow down")),
      run: { isCancelled: () => cancel, sleep: () => Promise.resolve(void (cancel = true)) },
    });
    expect(result.cancelled).toBe(true);
    expect(result.outcomes).toEqual([]);
  });
});

describe("runImport: a 409 while updating", () => {
  const collision = () => Promise.reject(problem(409, "A product with that name already exists"));

  it("is a failure with the server's sentence, never a skip: a rename collision must be seen", async () => {
    const { result } = await run([rename], { send: collision });
    expect(result).toMatchObject({ updated: 0, skipped: 0, failed: 1 });
    expect(result.outcomes[0]).toMatchObject({
      status: "failed",
      message: "A product with that name already exists",
      changes: ["Name"],
    });
  });

  it("is a failure for a new record too: the sheet's values were not applied", async () => {
    const { result } = await run([[null, "NEW-1", "Washer", null]], { create: collision });
    expect(result).toMatchObject({ created: 0, skipped: 0, failed: 1 });
  });

  it("is still a skip when adding: re-importing a file stays safe", async () => {
    const { result } = await run([[null, "NEW-1", "Washer", null]], { create: collision, mode: "add" });
    expect(result).toMatchObject({ created: 0, skipped: 1, failed: 0 });
  });
});

describe("runImport: other errors while updating", () => {
  it("fails the row once with the server's own sentence and goes on to the next", async () => {
    let sent = 0;
    const { result, send } = await run(
      [
        ["p-1", null, "A", null],
        ["p-2", null, "B", null],
      ],
      {
        send: () => {
          sent += 1;
          return sent === 1 ? Promise.reject(problem(422, "Name is too long")) : Promise.resolve();
        },
      },
    );
    expect(send).toHaveBeenCalledTimes(2);
    expect(result.outcomes.map((o) => [o.status, o.message])).toEqual([
      ["failed", "Name is too long"],
      ["updated", null],
    ]);
  });

  it("does not retry a refusal, or a server error", async () => {
    for (const status of [403, 404, 500]) {
      const { send, result } = await run([["p-1", null, "A", null]], { send: () => Promise.reject(problem(status, "No")) });
      expect(send).toHaveBeenCalledTimes(1);
      expect(result.failed).toBe(1);
    }
  });

  it("says when earlier changes to the same row were already saved", async () => {
    const steps = (): ExtraStep[] => [{ changed: ["Stage"], run: () => Promise.reject(problem(409, "A deal in 'won' cannot move")) }];
    const { result } = await run([["p-1", null, "A", null]], { extras: steps });
    expect(result.outcomes[0]).toMatchObject({
      status: "failed",
      message: `A deal in 'won' cannot move ${PARTLY_SAVED_MESSAGE}`,
      changes: ["Name", "Stage"],
    });
  });

  it("does not say so when the first request is the one that failed", async () => {
    const steps = (): ExtraStep[] => [{ changed: ["Stage"], run: () => Promise.resolve() }];
    const { result } = await run([["p-1", null, "A", null]], { extras: steps, send: () => Promise.reject(problem(422, "Bad")) });
    expect(result.outcomes[0]?.message).toBe("Bad");
  });
});

describe("runImport: cancelling an update", () => {
  it("does not start a row after the cancel", async () => {
    let cancel = false;
    const { result, calls } = await run(
      [
        ["p-1", null, "A", null],
        ["p-2", null, "B", null],
      ],
      { send: () => Promise.resolve(void (cancel = true)), run: { isCancelled: () => cancel } },
    );
    expect(calls).toEqual(['patch p-1 {"name":"A"}']);
    expect(result).toMatchObject({ cancelled: true, updated: 1 });
  });

  it("reports a row that was stopped half way as failed, saying what was saved, so it never vanishes", async () => {
    let cancel = false;
    const steps = (): ExtraStep[] => [{ changed: ["Stage"], run: () => Promise.resolve() }];
    const { result, calls } = await run([["p-1", null, "A", null]], {
      extras: steps,
      send: () => Promise.resolve(void (cancel = true)),
      run: { isCancelled: () => cancel },
    });
    expect(calls).toEqual(['patch p-1 {"name":"A"}']);
    expect(result.cancelled).toBe(true);
    expect(result.outcomes).toEqual([
      { row: 2, status: "failed", message: STOPPED_PARTWAY_MESSAGE, id: undefined, changes: ["Name", "Stage"], label: "SKU-1" },
    ]);
  });

  it("leaves no outcome for a row cancelled before its first request", async () => {
    const { result, calls } = await run([["p-1", null, "A", null]], { run: { isCancelled: () => true } });
    expect(calls).toEqual([]);
    expect(result).toMatchObject({ cancelled: true, outcomes: [] });
  });
});
