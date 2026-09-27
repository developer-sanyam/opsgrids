/**
 * Import re-checks an update against the records as they are when it is pressed, once the ones it
 * was previewed against are more than two minutes old: an update sends what the preview said it
 * would, so if anything the user reviewed would now be different, the new preview is shown and
 * nothing is sent.
 */

import { describe, expect, it, vi } from "vitest";

import { ApiError } from "../http";
import { RECORDS_CHANGED_NOTICE, SNAPSHOT_MAX_AGE_MS, createExchange } from "./exchange";
import { fileWithRows, makeProductEntity, makeProductUpdate, storedProduct } from "./testkit";
import type { ProductItem } from "./testkit";
import type { DataExchangeState } from "./types";

const HEADERS = ["Record ID", "SKU *", "Name *", "Price"];
type Cell = string | null;

const file = (rows: readonly (readonly Cell[])[] = [["p-1", null, "Bracket", "12.50"]]) => fileWithRows(HEADERS, rows);

/** The records as `count` calls to the loader will see them: the last version is kept for every call after. */
function versions(...list: readonly (readonly ProductItem[])[]) {
  let call = 0;
  const calls: number[] = [];
  return {
    calls,
    load: (): Promise<readonly ProductItem[]> => {
      calls.push(call);
      const version = list[Math.min(call, list.length - 1)] ?? [];
      call += 1;
      return Promise.resolve(version);
    },
  };
}

function setUp(load: () => Promise<readonly ProductItem[]>) {
  const clock = { now: 1_000_000 };
  const sent: { id: string; body: Readonly<Record<string, unknown>> }[] = [];
  const created: string[] = [];
  const entity = makeProductEntity({
    recordIdKey: "id",
    create: (payload) => {
      created.push(payload.sku);
      return Promise.resolve({ id: "new-id" });
    },
    update: makeProductUpdate({
      load,
      send: (existing, body) => {
        sent.push({ id: existing.id, body });
        return Promise.resolve();
      },
    }),
  });
  const states: DataExchangeState[] = [];
  const exchange = createExchange({
    entity: () => entity,
    filters: () => undefined,
    publish: (state) => states.push(state),
    imported: () => undefined,
    now: () => clock.now,
  });
  return {
    exchange,
    clock,
    sent,
    created,
    states,
    latest: () => states.at(-1),
    /** The records go stale: more time than a snapshot may be old has passed. */
    ageBy: (ms: number) => {
      clock.now += ms;
    },
  };
}

const original = [storedProduct(1), storedProduct(2)];

describe("Import with records that are not old", () => {
  it("sends the plan as reviewed, without reading the records again", async () => {
    const records = versions(original);
    const { exchange, ageBy, sent } = setUp(records.load);
    await exchange.actions.setMode("addAndUpdate");
    await exchange.actions.openFile(await file());
    ageBy(SNAPSHOT_MAX_AGE_MS);
    await exchange.actions.startImport();
    expect(records.calls).toHaveLength(1);
    expect(sent).toEqual([{ id: "p-1", body: { name: "Bracket", price_minor: 1250 } }]);
  });
});

describe("Import with records more than two minutes old", () => {
  it("reads them again, and sends the fresh plan when nothing the user reviewed has changed", async () => {
    const records = versions(original, original);
    const { exchange, ageBy, sent, states, latest } = setUp(records.load);
    await exchange.actions.setMode("addAndUpdate");
    await exchange.actions.openFile(await file());
    const from = states.length;
    ageBy(SNAPSHOT_MAX_AGE_MS + 1);
    await exchange.actions.startImport();
    expect(records.calls).toHaveLength(2);
    expect(sent).toEqual([{ id: "p-1", body: { name: "Bracket", price_minor: 1250 } }]);
    // The dialog said it was reading, showed the preview again, then imported: each phase once, in order.
    expect([...new Set(states.slice(from).map((state) => state.phase))]).toEqual(["reading", "preview", "importing", "done"]);
    expect(latest()?.summary).toMatchObject({ updated: 1, failed: 0 });
  });

  it("does not send a row the user never reviewed: a record that changed under the preview shows the new preview, with a notice, and sends nothing", async () => {
    // While the preview was open a colleague set the price the file also sets.
    const later = [storedProduct(1, { priceMinor: 1250 }), storedProduct(2)];
    const records = versions(original, later);
    const { exchange, ageBy, sent, latest } = setUp(records.load);
    await exchange.actions.setMode("addAndUpdate");
    await exchange.actions.openFile(await file());
    expect(latest()?.preview?.rows[0]).toMatchObject({ changes: ["Name", "Price"] });
    ageBy(SNAPSHOT_MAX_AGE_MS + 1);
    await exchange.actions.startImport();

    expect(sent).toEqual([]);
    expect(latest()).toMatchObject({ phase: "preview", notice: RECORDS_CHANGED_NOTICE });
    expect(latest()?.preview?.rows[0]).toMatchObject({ action: "update", changes: ["Name"] });
  });

  it("sends the refreshed plan when Import is chosen again, without a third read: the records are fresh now", async () => {
    const later = [storedProduct(1, { priceMinor: 1250 }), storedProduct(2)];
    const records = versions(original, later);
    const { exchange, ageBy, sent, latest } = setUp(records.load);
    await exchange.actions.setMode("addAndUpdate");
    await exchange.actions.openFile(await file());
    ageBy(SNAPSHOT_MAX_AGE_MS + 1);
    await exchange.actions.startImport();
    await exchange.actions.startImport();
    expect(records.calls).toHaveLength(2);
    // Only what is still different: the price a colleague already set is left as it is.
    expect(sent).toEqual([{ id: "p-1", body: { name: "Bracket" } }]);
    expect(latest()?.phase).toBe("done");
  });

  it("notices a change to a row that the preview table does not list: it compares every row, not the first 200", async () => {
    const many = Array.from({ length: 204 }, (_, index): Cell[] => [null, `NEW-${index}`, `Item ${index}`, null]);
    const rows: Cell[][] = [...many, ["p-1", null, "Bracket", null]];
    const later = [storedProduct(1, { name: "Bracket" }), storedProduct(2)];
    const records = versions(original, later);
    const { exchange, ageBy, sent, created, latest } = setUp(records.load);
    await exchange.actions.setMode("addAndUpdate");
    await exchange.actions.openFile(await file(rows));
    expect(latest()?.preview?.rows).toHaveLength(200);
    expect(latest()?.preview).toMatchObject({ total: 205, create: 204, update: 1 });
    ageBy(SNAPSHOT_MAX_AGE_MS + 1);
    await exchange.actions.startImport();
    expect(created).toEqual([]);
    expect(sent).toEqual([]);
    expect(latest()).toMatchObject({ phase: "preview", notice: RECORDS_CHANGED_NOTICE });
    expect(latest()?.preview).toMatchObject({ create: 204, update: 0, unchanged: 1 });
  });

  it("leaves the review alone, and says why, when the records cannot be read again", async () => {
    let call = 0;
    const { exchange, ageBy, sent, latest } = setUp(() => {
      call += 1;
      return call === 2
        ? Promise.reject(new ApiError(503, "The service is busy", { status: 503, detail: "The service is busy" }))
        : Promise.resolve(original);
    });
    await exchange.actions.setMode("addAndUpdate");
    await exchange.actions.openFile(await file());
    const reviewed = latest()?.preview;
    ageBy(SNAPSHOT_MAX_AGE_MS + 1);
    await exchange.actions.startImport();

    expect(sent).toEqual([]);
    expect(latest()?.phase).toBe("preview");
    expect(latest()?.preview).toBe(reviewed);
    expect(latest()?.notice).toContain("Nothing was imported, because the current records could not be checked.");
    expect(latest()?.notice).toContain("The lists this file is checked against could not be loaded.");

    // Try again: the records read this time, and the import goes through.
    await exchange.actions.startImport();
    expect(sent).toEqual([{ id: "p-1", body: { name: "Bracket", price_minor: 1250 } }]);
  });

  it("reads once, and sends once, when Import is pressed twice while it reads", async () => {
    const records = versions(original, original);
    const { exchange, ageBy, sent } = setUp(records.load);
    await exchange.actions.setMode("addAndUpdate");
    await exchange.actions.openFile(await file());
    ageBy(SNAPSHOT_MAX_AGE_MS + 1);
    await Promise.all([exchange.actions.startImport(), exchange.actions.startImport()]);
    expect(records.calls).toHaveLength(2);
    expect(sent).toHaveLength(1);
  });

  /** A loader whose second read waits for the test to let it finish. */
  function slowSecondRead() {
    const gate: { release: (items: readonly ProductItem[]) => void; calls: number } = {
      release: () => undefined,
      calls: 0,
    };
    const load = (): Promise<readonly ProductItem[]> => {
      gate.calls += 1;
      return gate.calls === 1
        ? Promise.resolve(original)
        : new Promise<readonly ProductItem[]>((resolve) => {
            gate.release = resolve;
          });
    };
    return { gate, load };
  }

  it("shows the dialog reading while it reads, sends nothing yet, and ignores a mode change meanwhile", async () => {
    const { gate, load } = slowSecondRead();
    const { exchange, ageBy, sent, latest } = setUp(load);
    await exchange.actions.setMode("addAndUpdate");
    await exchange.actions.openFile(await file());
    ageBy(SNAPSHOT_MAX_AGE_MS + 1);
    const pressed = exchange.actions.startImport();
    await vi.waitFor(() => expect(gate.calls).toBe(2));
    expect(latest()).toMatchObject({ phase: "reading", preview: null });
    await exchange.actions.setMode("add");
    expect(latest()?.mode).toBe("addAndUpdate");
    expect(sent).toEqual([]);
    gate.release(original);
    await pressed;
    expect(sent).toEqual([{ id: "p-1", body: { name: "Bracket", price_minor: 1250 } }]);
  });

  it("drops what it read when the dialog is reset meanwhile, and sends nothing", async () => {
    const { gate, load } = slowSecondRead();
    const { exchange, ageBy, sent, latest } = setUp(load);
    await exchange.actions.setMode("addAndUpdate");
    await exchange.actions.openFile(await file());
    ageBy(SNAPSHOT_MAX_AGE_MS + 1);
    const pressed = exchange.actions.startImport();
    await vi.waitFor(() => expect(gate.calls).toBe(2));
    exchange.actions.reset();
    gate.release(original);
    await pressed;
    expect(sent).toEqual([]);
    expect(latest()).toMatchObject({ phase: "menu", mode: "add", preview: null });
  });

  it("never reads again in add mode, whatever the time: nothing was loaded to be stale", async () => {
    const records = versions(original);
    const { exchange, ageBy, created, latest } = setUp(records.load);
    await exchange.actions.openFile(await file([[null, "NEW-1", "Washer", null]]));
    ageBy(10 * SNAPSHOT_MAX_AGE_MS);
    await exchange.actions.startImport();
    expect(records.calls).toEqual([]);
    expect(created).toEqual(["NEW-1"]);
    expect(latest()?.phase).toBe("done");
  });

  it("clears the notice when the user chooses another file or another mode", async () => {
    const later = [storedProduct(1, { priceMinor: 1250 }), storedProduct(2)];
    const records = versions(original, later, later);
    const { exchange, ageBy, latest } = setUp(records.load);
    await exchange.actions.setMode("addAndUpdate");
    await exchange.actions.openFile(await file());
    ageBy(SNAPSHOT_MAX_AGE_MS + 1);
    await exchange.actions.startImport();
    expect(latest()?.notice).toBe(RECORDS_CHANGED_NOTICE);
    await exchange.actions.setMode("add");
    expect(latest()?.notice).toBeNull();
  });
});
