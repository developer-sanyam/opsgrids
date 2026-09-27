import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../http";
import { createExchange } from "./exchange";
import { ReferenceListError } from "./lookups";
import { RECORD_ID_UPDATE_HINT } from "./preview";
import { fileWithRows, makeProductEntity, makeProductUpdate, productLookups, storedProduct } from "./testkit";
import type { ProductItem, ProductPayload } from "./testkit";
import type { DataExchangeState, ImportContext, ImportSummary } from "./types";
import { readWorkbook } from "./workbook";

vi.mock("./workbook", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./workbook")>();
  return { ...actual, readWorkbook: vi.fn(actual.readWorkbook) };
});

const HEADERS = ["Record ID", "SKU *", "Name *", "Price"];
type Cell = string | null;

beforeEach(() => {
  vi.mocked(readWorkbook).mockClear();
});

const stored: ProductItem[] = [storedProduct(1), storedProduct(2)];

function deferred<T>() {
  let settle: (value: T) => void = () => undefined;
  let fail: (error: unknown) => void = () => undefined;
  const promise = new Promise<T>((resolve, reject) => {
    settle = resolve;
    fail = reject;
  });
  return { promise, resolve: settle, reject: fail };
}

interface Setup {
  load?: (ctx: ImportContext) => Promise<readonly ProductItem[]>;
  send?: (existing: ProductItem, body: Readonly<Record<string, unknown>>) => Promise<void>;
  create?: (payload: ProductPayload) => Promise<{ id?: string } | undefined>;
  canUpdate?: boolean;
}

function setUp(options: Setup = {}) {
  const load = vi.fn<(ctx: ImportContext) => Promise<readonly ProductItem[]>>(options.load ?? (() => Promise.resolve(stored)));
  const send = vi.fn<NonNullable<Setup["send"]>>(options.send ?? (() => Promise.resolve()));
  const create = vi.fn<NonNullable<Setup["create"]>>(options.create ?? (() => Promise.resolve({ id: "new-id" })));
  const entity = makeProductEntity({
    recordIdKey: "id",
    create: (payload) => create(payload),
    ...(options.canUpdate === false
      ? {}
      : { update: makeProductUpdate({ load: (ctx) => load(ctx), send: (existing, body) => send(existing, body) }) }),
  });
  const states: DataExchangeState[] = [];
  const summaries: ImportSummary[] = [];
  const exchange = createExchange({
    entity: () => entity,
    filters: () => undefined,
    publish: (state) => states.push(state),
    imported: (summary) => summaries.push(summary),
  });
  return {
    exchange,
    load,
    send,
    create,
    states,
    summaries,
    latest: () => states.at(-1),
    /** The phases published since `from`. */
    phasesSince: (from: number) => states.slice(from).map((state) => state.phase),
  };
}

const file = (rows: readonly (readonly Cell[])[] = [["p-1", null, "Bracket", null]]) => fileWithRows(HEADERS, rows);

describe("choosing the mode", () => {
  it("is add until the user chooses, and choosing before a file is read loads nothing", async () => {
    const { exchange, load, latest } = setUp();
    expect(latest()).toBeUndefined();
    await exchange.actions.setMode("addAndUpdate");
    expect(latest()?.mode).toBe("addAndUpdate");
    expect(load).not.toHaveBeenCalled();
    await exchange.actions.setMode("add");
    expect(latest()?.mode).toBe("add");
  });

  it("is ignored for a list that has no update spec", async () => {
    const { exchange, states } = setUp({ canUpdate: false });
    await exchange.actions.setMode("addAndUpdate");
    expect(states).toEqual([]);
  });

  it("is applied to a file opened afterwards: the preview is in that mode", async () => {
    const { exchange, latest } = setUp();
    await exchange.actions.setMode("addAndUpdate");
    await exchange.actions.openFile(await file());
    expect(latest()?.preview).toMatchObject({ mode: "addAndUpdate", update: 1, valid: 1 });
  });

  it("goes back to add when the dialog is reset: overwriting is chosen each time, never inherited", async () => {
    const { exchange, latest } = setUp();
    await exchange.actions.setMode("addAndUpdate");
    await exchange.actions.openFile(await file());
    exchange.actions.reset();
    expect(latest()).toMatchObject({ phase: "menu", mode: "add", preview: null });
  });
});

describe("the records to update are loaded for each file, and shared while a load is in flight", () => {
  it("does not load them at all in add mode", async () => {
    const { exchange, load, latest } = setUp();
    await exchange.actions.openFile(await file());
    expect(load).not.toHaveBeenCalled();
    expect(latest()?.preview).toMatchObject({ mode: "add", invalid: 1 });
  });

  it("loads them with the lookups and the dialog's signal", async () => {
    const { exchange, load } = setUp();
    await exchange.actions.setMode("addAndUpdate");
    await exchange.actions.openFile(await file());
    expect(load).toHaveBeenCalledTimes(1);
    const ctx = load.mock.calls[0]?.[0];
    expect(ctx?.lookups).toBe(productLookups);
    expect(ctx?.signal).toBeInstanceOf(AbortSignal);
    expect(ctx?.signal.aborted).toBe(false);
  });

  it("reads them again for every file that is opened: it may have been edited for an hour since the last load", async () => {
    const { exchange, load } = setUp();
    await exchange.actions.setMode("addAndUpdate");
    await exchange.actions.openFile(await file());
    await exchange.actions.openFile(await file([[null, "SKU-2", "Nut", null]]));
    expect(load).toHaveBeenCalledTimes(2);
  });

  it("does not compare a re-opened file with the first load", async () => {
    let call = 0;
    const { exchange, latest } = setUp({
      load: () => Promise.resolve(call++ === 0 ? stored : [storedProduct(1, { name: "Bracket" }), storedProduct(2)]),
    });
    await exchange.actions.setMode("addAndUpdate");
    await exchange.actions.openFile(await file());
    expect(latest()?.preview?.rows[0]).toMatchObject({ action: "update", changes: ["Name"] });
    await exchange.actions.openFile(await file());
    // With the first load this would still say Name changes; the record already has it.
    expect(latest()?.preview?.rows[0]).toMatchObject({ action: "unchanged" });
  });

  it("answers a change of mode from the load it already has, without a second read", async () => {
    const { exchange, load } = setUp();
    await exchange.actions.openFile(await file());
    await exchange.actions.setMode("addAndUpdate");
    await exchange.actions.setMode("add");
    await exchange.actions.setMode("addAndUpdate");
    expect(load).toHaveBeenCalledTimes(1);
  });

  it("shares one load that is in flight between everyone who asks", async () => {
    const gate = deferred<readonly ProductItem[]>();
    const { exchange, load, latest } = setUp({ load: () => gate.promise });
    await exchange.actions.setMode("addAndUpdate");
    const first = exchange.actions.openFile(await file());
    await vi.waitFor(() => expect(load).toHaveBeenCalledTimes(1));
    const second = exchange.actions.openFile(await file([[null, "SKU-2", "Nut", null]]));
    await Promise.resolve();
    expect(load).toHaveBeenCalledTimes(1);
    gate.resolve(stored);
    await Promise.all([first, second]);
    expect(load).toHaveBeenCalledTimes(1);
    expect(latest()?.phase).toBe("preview");
  });

  it("does not keep a load that failed: the next try asks again", async () => {
    let attempt = 0;
    const { exchange, load, latest } = setUp({
      load: () => {
        attempt += 1;
        return attempt === 1 ? Promise.reject(new ApiError(503, "The service is busy", { status: 503, detail: "The service is busy" })) : Promise.resolve(stored);
      },
    });
    await exchange.actions.setMode("addAndUpdate");
    await exchange.actions.openFile(await file());
    expect(latest()?.phase).toBe("error");
    expect(latest()?.error).toContain("could not be loaded");
    exchange.actions.reset();
    await exchange.actions.setMode("addAndUpdate");
    await exchange.actions.openFile(await file());
    expect(load).toHaveBeenCalledTimes(2);
    expect(latest()?.phase).toBe("preview");
  });

  it("shows a list that is too long, or that changed while it was read, as the reason, not as a broken file", async () => {
    for (const message of [
      "A list this import checks against has more than 10,000 records, which is more than an import can look up.",
      "A list this import checks against changed while it was being read. Try again in a moment.",
    ]) {
      const { exchange, latest } = setUp({ load: () => Promise.reject(new ReferenceListError(message)) });
      await exchange.actions.setMode("addAndUpdate");
      await exchange.actions.openFile(await file());
      expect(latest()).toMatchObject({ phase: "error", error: message });
    }
  });

  it("asks again after an import that updated a record, and after one that created one", async () => {
    for (const rows of [
      [["p-1", null, "Bracket", null]] as const,
      [[null, "NEW-1", "Washer", null]] as const,
    ]) {
      const { exchange, load } = setUp();
      await exchange.actions.setMode("addAndUpdate");
      await exchange.actions.openFile(await file(rows));
      await exchange.actions.startImport();
      exchange.actions.reset();
      await exchange.actions.setMode("addAndUpdate");
      await exchange.actions.openFile(await file(rows));
      expect(load).toHaveBeenCalledTimes(2);
    }
  });

  it("reads them again for the next file after an import that changed nothing, as after any other", async () => {
    const { exchange, load } = setUp({ send: () => Promise.reject(new ApiError(422, "No", { status: 422, detail: "No" })) });
    await exchange.actions.setMode("addAndUpdate");
    await exchange.actions.openFile(await file());
    await exchange.actions.startImport();
    await exchange.actions.openFile(await file());
    expect(load).toHaveBeenCalledTimes(2);
  });

  it("drops them when the dialog is reset, and when it is torn down", async () => {
    const { exchange, load } = setUp();
    await exchange.actions.setMode("addAndUpdate");
    await exchange.actions.openFile(await file());
    exchange.actions.reset();
    await exchange.actions.setMode("addAndUpdate");
    await exchange.actions.openFile(await file());
    expect(load).toHaveBeenCalledTimes(2);

    exchange.dispose();
    exchange.attach();
    await exchange.actions.openFile(await file());
    expect(load).toHaveBeenCalledTimes(3);
  });
});

describe("changing the mode on a preview", () => {
  it("checks the file again in the new mode without reading it again", async () => {
    const { exchange, latest, states } = setUp();
    await exchange.actions.openFile(await file());
    expect(latest()?.preview).toMatchObject({ mode: "add", valid: 0, invalid: 1 });
    expect(latest()?.preview?.rows[0]?.error).toBe(
      `This row has a Record ID, so it came from an export and is already in the system. Clear the Record ID cell to add it as a new record. ${RECORD_ID_UPDATE_HINT}`,
    );
    expect(readWorkbook).toHaveBeenCalledTimes(1);

    const from = states.length;
    await exchange.actions.setMode("addAndUpdate");
    expect(readWorkbook).toHaveBeenCalledTimes(1);
    expect(latest()).toMatchObject({ phase: "preview", mode: "addAndUpdate" });
    expect(latest()?.preview).toMatchObject({ mode: "addAndUpdate", update: 1, valid: 1, invalid: 0 });
    // The records had to be loaded, so the wait is shown as a wait.
    expect(states.slice(from).map((state) => state.phase)).toEqual(["reading", "preview"]);
    expect(states[from]).toMatchObject({ mode: "addAndUpdate", preview: null });
  });

  it("answers at once, with no wait shown and no new load, when the records are already here", async () => {
    const { exchange, latest, load, states } = setUp();
    await exchange.actions.openFile(await file());
    await exchange.actions.setMode("addAndUpdate");
    const from = states.length;

    await exchange.actions.setMode("add");
    expect(latest()?.preview).toMatchObject({ mode: "add", invalid: 1 });
    await exchange.actions.setMode("addAndUpdate");
    expect(latest()?.preview).toMatchObject({ mode: "addAndUpdate", update: 1 });

    expect(states.slice(from).every((state) => state.phase === "preview")).toBe(true);
    expect(load).toHaveBeenCalledTimes(1);
    expect(readWorkbook).toHaveBeenCalledTimes(1);
  });

  it("cannot send a row from the other mode's preview while the new one is being worked out", async () => {
    const gate = deferred<readonly ProductItem[]>();
    const { exchange, latest, send, create } = setUp({ load: () => gate.promise });
    await exchange.actions.openFile(await file([[null, "NEW-1", "Washer", null]]));
    const switching = exchange.actions.setMode("addAndUpdate");
    await Promise.resolve();
    expect(latest()?.phase).toBe("reading");
    await exchange.actions.startImport();
    expect(create).not.toHaveBeenCalled();
    expect(send).not.toHaveBeenCalled();
    gate.resolve(stored);
    await switching;
    expect(latest()?.phase).toBe("preview");
  });

  it("drops the result when the dialog was reset while the records were loading", async () => {
    const gate = deferred<readonly ProductItem[]>();
    const { exchange, latest } = setUp({ load: () => gate.promise });
    await exchange.actions.openFile(await file());
    const switching = exchange.actions.setMode("addAndUpdate");
    await Promise.resolve();
    exchange.actions.reset();
    gate.resolve(stored);
    await switching;
    expect(latest()).toMatchObject({ phase: "menu", mode: "add", preview: null });
  });

  it("says why when the records cannot be loaded, instead of leaving a preview that is for the other mode", async () => {
    const { exchange, latest } = setUp({ load: () => Promise.reject(new ReferenceListError("Too many records.")) });
    await exchange.actions.openFile(await file());
    await exchange.actions.setMode("addAndUpdate");
    expect(latest()).toMatchObject({ phase: "error", error: "Too many records." });
  });

  it("does nothing while rows are being sent", async () => {
    const gate = deferred<{ id?: string }>();
    const { exchange, latest, load } = setUp({ create: () => gate.promise });
    await exchange.actions.openFile(await file([[null, "NEW-1", "Washer", null]]));
    const running = exchange.actions.startImport();
    await vi.waitFor(() => expect(latest()?.phase).toBe("importing"));
    await exchange.actions.setMode("addAndUpdate");
    expect(latest()?.mode).toBe("add");
    expect(load).not.toHaveBeenCalled();
    gate.resolve({ id: "x" });
    await running;
  });
});

describe("importing in update mode", () => {
  it("sends the changes, adds the new records, sends nothing for rows that match, and counts each", async () => {
    const { exchange, latest, send, create, summaries } = setUp();
    await exchange.actions.setMode("addAndUpdate");
    await exchange.actions.openFile(
      await file([
        ["p-1", null, "Bracket", null],
        [null, "NEW-1", "Washer", null],
        ["p-2", null, null, null],
        ["p-404", null, null, null],
      ]),
    );
    expect(latest()?.preview).toMatchObject({ total: 4, update: 1, create: 1, unchanged: 1, invalid: 1, valid: 2 });
    await exchange.actions.startImport();

    expect(send).toHaveBeenCalledTimes(1);
    expect(send.mock.calls[0]?.[0].id).toBe("p-1");
    expect(send.mock.calls[0]?.[1]).toEqual({ name: "Bracket" });
    expect(create).toHaveBeenCalledTimes(1);

    const summary = latest()?.summary;
    expect(summary).toMatchObject({ created: 1, updated: 1, unchanged: 1, invalid: 1, failed: 0, skipped: 0, cancelled: false });
    expect(summary?.outcomes.map((o) => [o.row, o.status])).toEqual([
      [2, "updated"],
      [3, "created"],
      [4, "unchanged"],
      [5, "invalid"],
    ]);
    expect(latest()?.progress).toBeNull();
    expect(summaries).toHaveLength(1);
  });

  it("starts the progress at zero for the rows that will be sent: rows that match are not among them", async () => {
    const { exchange, states } = setUp();
    await exchange.actions.setMode("addAndUpdate");
    await exchange.actions.openFile(await file([["p-1", null, "Bracket", null], ["p-2", null, null, null]]));
    await exchange.actions.startImport();
    expect(states.find((state) => state.phase === "importing")?.progress).toEqual({
      done: 0,
      total: 1,
      created: 0,
      updated: 0,
      skipped: 0,
      failed: 0,
      waiting: null,
    });
  });

  it("makes a 409 a failure, not a skip", async () => {
    const collision = new ApiError(409, "A product with that name already exists", {
      status: 409,
      detail: "A product with that name already exists",
    });
    const { exchange, latest } = setUp({ send: () => Promise.reject(collision) });
    await exchange.actions.setMode("addAndUpdate");
    await exchange.actions.openFile(await file());
    await exchange.actions.startImport();
    expect(latest()?.summary).toMatchObject({ failed: 1, skipped: 0, updated: 0 });
  });

  it("tells the screen when only updates happened, and not when nothing was created or updated", async () => {
    const changed = setUp();
    await changed.exchange.actions.setMode("addAndUpdate");
    await changed.exchange.actions.openFile(await file());
    await changed.exchange.actions.startImport();
    expect(changed.summaries).toHaveLength(1);
    expect(changed.summaries[0]).toMatchObject({ created: 0, updated: 1 });

    const same = setUp();
    await same.exchange.actions.setMode("addAndUpdate");
    await same.exchange.actions.openFile(await file([["p-2", null, null, null]]));
    await same.exchange.actions.startImport();
    expect(same.latest()?.summary).toMatchObject({ updated: 0, unchanged: 1 });
    expect(same.summaries).toEqual([]);
  });

  it("does not start when every row matches its record and there is nothing to send", async () => {
    const { exchange, latest, send } = setUp();
    await exchange.actions.setMode("addAndUpdate");
    await exchange.actions.openFile(await file([["p-2", null, null, null]]));
    expect(latest()?.preview).toMatchObject({ valid: 0, unchanged: 1 });
    await exchange.actions.startImport();
    expect(send).not.toHaveBeenCalled();
  });

  it("runs an add exactly as before when the mode was never changed", async () => {
    const { exchange, latest, create, send, load } = setUp();
    await exchange.actions.openFile(await fileWithRows(["SKU *", "Name *"], [["A-1", "Bracket"]]));
    await exchange.actions.startImport();
    expect(create).toHaveBeenCalledTimes(1);
    expect(send).not.toHaveBeenCalled();
    expect(load).not.toHaveBeenCalled();
    expect(latest()?.summary).toMatchObject({ created: 1, updated: 0, unchanged: 0, skipped: 0 });
  });
});
