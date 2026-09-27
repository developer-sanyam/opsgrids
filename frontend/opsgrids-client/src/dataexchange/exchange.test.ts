import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../http";
import { COOLDOWN_MS, COOLDOWN_REQUEST_THRESHOLD, createExchange } from "./exchange";
import { ReferenceListError } from "./lookups";
import { fileWithRows, makeProductEntity } from "./testkit";
import type { ProductEntityOptions } from "./testkit";
import type { DataExchangeState, ImportSummary } from "./types";

const HEADERS = ["SKU *", "Name *"];

beforeEach(() => {
  URL.createObjectURL = vi.fn(() => "blob:test");
  URL.revokeObjectURL = vi.fn();
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
});

/** An exchange whose every publish and every `imported` call lands in one ordered log. */
function loggedExchange(options: ProductEntityOptions = {}) {
  const log: string[] = [];
  const states: DataExchangeState[] = [];
  const summaries: ImportSummary[] = [];
  const entity = makeProductEntity(options);
  const exchange = createExchange({
    entity: () => entity,
    filters: () => undefined,
    publish: (state) => {
      states.push(state);
      log.push(`state:${state.phase}`);
    },
    imported: (summary) => {
      summaries.push(summary);
      log.push("imported");
    },
  });
  return { exchange, log, states, summaries, latest: () => states.at(-1) };
}

describe("createExchange: a reference list that cannot be read", () => {
  const tooLong = () =>
    Promise.reject(new ReferenceListError("A list this import checks against has more than 10,000 records."));

  it("tells the user the real reason instead of blaming their file", async () => {
    const { exchange, latest } = loggedExchange({ loadLookups: tooLong });
    await exchange.actions.openFile(await fileWithRows(HEADERS, [["A-1", "Bracket"]]));
    expect(latest()?.phase).toBe("error");
    expect(latest()?.error).toBe("A list this import checks against has more than 10,000 records.");
    expect(latest()?.error).not.toMatch(/could not be read/);
  });

  it("says it in the notice when the sample sheet or the export needs that list", async () => {
    const { exchange, latest } = loggedExchange({ loadLookups: tooLong });
    await exchange.actions.downloadTemplate();
    expect(latest()?.notice).toMatch(/more than 10,000 records/);
    await exchange.actions.exportAll();
    expect(latest()?.notice).toMatch(/more than 10,000 records/);
  });
});

describe("createExchange", () => {
  it("tells the screen after the state says done, never before", async () => {
    const { exchange, log, summaries, latest } = loggedExchange();
    await exchange.actions.openFile(await fileWithRows(HEADERS, [["A-1", "Bracket"]]));
    await exchange.actions.startImport();
    expect(log.slice(-2)).toEqual(["state:done", "imported"]);
    expect(log.filter((entry) => entry === "imported")).toHaveLength(1);
    expect(summaries[0]).toBe(latest()?.summary);
  });

  it("does not tell the screen when no row was created", async () => {
    const { exchange, log, latest } = loggedExchange({ create: () => Promise.reject(new Error("down")) });
    await exchange.actions.openFile(await fileWithRows(HEADERS, [["A-1", "Bracket"]]));
    await exchange.actions.startImport();
    expect(latest()?.phase).toBe("done");
    expect(log).not.toContain("imported");
  });

  it("resolves startImport only once the run has ended", async () => {
    let release: () => void = () => undefined;
    const gate = new Promise<undefined>((resolve) => {
      release = () => resolve(undefined);
    });
    const { exchange, latest } = loggedExchange({ create: () => gate });
    await exchange.actions.openFile(await fileWithRows(HEADERS, [["A-1", "Bracket"]]));
    let ended = false;
    const running = exchange.actions.startImport().then(() => {
      ended = true;
    });
    await Promise.resolve();
    expect(latest()?.phase).toBe("importing");
    expect(ended).toBe(false);
    release();
    await running;
    expect(ended).toBe(true);
    expect(latest()?.phase).toBe("done");
  });

  it("stops publishing after dispose, and arms again after attach", async () => {
    const { exchange, states, latest } = loggedExchange();
    exchange.dispose();
    const before = states.length;
    await exchange.actions.openFile(await fileWithRows(HEADERS, [["A-1", "Bracket"]]));
    expect(states).toHaveLength(before);

    exchange.attach();
    await exchange.actions.openFile(await fileWithRows(HEADERS, [["A-1", "Bracket"]]));
    expect(states.length).toBeGreaterThan(before);
    expect(latest()?.phase).toBe("preview");
  });
});

describe("createExchange: the cooldown after a run that sent enough requests to matter", () => {
  function bigFile(rows: number) {
    return fileWithRows(
      HEADERS,
      Array.from({ length: rows }, (_, i) => [`A-${i}`, `Bracket ${i}`]),
    );
  }

  /**
   * A `sleep` that lets the runner's own per-row pacing through at once (it never asks for a full
   * second: `DEFAULT_MIN_INTERVAL_MS` is 350 ms), so a run of hundreds of rows costs no real test
   * time, but holds each of the cooldown's own one-second ticks until the test releases it. That
   * makes the countdown fully deterministic: nothing about it depends on how many other promises
   * happen to settle first, the way it would racing a real or a faked clock against exceljs's own
   * real I/O.
   */
  function controlledCooldownClock() {
    const pending: Array<() => void> = [];
    const sleep = (ms: number): Promise<void> => {
      if (ms !== 1000) return Promise.resolve();
      return new Promise((resolve) => pending.push(resolve));
    };
    /** Lets exactly one queued tick resolve, and its `publish` land, before returning. */
    const tick = async (): Promise<void> => {
      const next = pending.shift();
      if (next === undefined) throw new Error("no cooldown tick is waiting to be released");
      next();
      await Promise.resolve();
      await Promise.resolve();
    };
    return { sleep, tick };
  }

  it("opens once a run reaches the threshold, blocks Import, and clears itself", async () => {
    const created: string[] = [];
    const states: DataExchangeState[] = [];
    const clock = controlledCooldownClock();
    const entity = makeProductEntity({
      create: (payload) => {
        created.push(payload.sku);
        return Promise.resolve({ id: payload.sku });
      },
    });
    const exchange = createExchange({
      entity: () => entity,
      filters: () => undefined,
      publish: (state) => states.push(state),
      imported: () => undefined,
      sleep: clock.sleep,
    });
    const latest = () => states.at(-1);

    await exchange.actions.openFile(await bigFile(COOLDOWN_REQUEST_THRESHOLD));
    expect(latest()?.phase).toBe("preview");
    await exchange.actions.startImport();

    // Every row sent, and the cooldown's first tick is queued and held: exactly its full length.
    expect(created).toHaveLength(COOLDOWN_REQUEST_THRESHOLD);
    expect(latest()?.phase).toBe("done");
    expect(latest()?.cooldownSecondsLeft).toBe(COOLDOWN_MS / 1000);

    // A second file previews freely (nothing here is blocked but sending)...
    await exchange.actions.openFile(await fileWithRows(HEADERS, [["Z-1", "Plate"]]));
    expect(latest()?.phase).toBe("preview");
    const before = created.length;

    // ...but starting it while the cooldown is still counting down sends nothing at all.
    await exchange.actions.startImport();
    expect(created).toHaveLength(before);
    expect(latest()?.phase).toBe("preview");
    expect(latest()?.cooldownSecondsLeft).toBe(COOLDOWN_MS / 1000);

    // Releasing every tick counts it down one second at a time, then the engine clears it.
    for (let seconds = COOLDOWN_MS / 1000; seconds > 1; seconds -= 1) {
      await clock.tick();
      expect(latest()?.cooldownSecondsLeft).toBe(seconds - 1);
    }
    await clock.tick();
    expect(latest()?.cooldownSecondsLeft).toBeNull();

    // The same file can now go through.
    await exchange.actions.startImport();
    expect(created).toHaveLength(before + 1);
    expect(latest()?.phase).toBe("done");
    expect(latest()?.cooldownSecondsLeft).toBeNull();
  });

  it("never opens for a run that sent fewer requests than the threshold", async () => {
    const states: DataExchangeState[] = [];
    const clock = controlledCooldownClock();
    const entity = makeProductEntity({ create: () => Promise.resolve({ id: "new-id" }) });
    const exchange = createExchange({
      entity: () => entity,
      filters: () => undefined,
      publish: (state) => states.push(state),
      imported: () => undefined,
      sleep: clock.sleep,
    });
    const latest = () => states.at(-1);

    await exchange.actions.openFile(await bigFile(COOLDOWN_REQUEST_THRESHOLD - 1));
    await exchange.actions.startImport();

    expect(latest()?.phase).toBe("done");
    expect(states.every((state) => state.cooldownSecondsLeft === null)).toBe(true);
  });

  it("counts a row the server refused, not only one it created", async () => {
    // A row that fails still cost a request: the budget does not know the server said no.
    const states: DataExchangeState[] = [];
    const clock = controlledCooldownClock();
    let call = 0;
    const entity = makeProductEntity({
      create: () => {
        call += 1;
        return call % 2 === 0
          ? Promise.reject(new ApiError(422, "Request failed with 422", { status: 422, detail: "Bad value" }))
          : Promise.resolve({ id: `id-${call}` });
      },
    });
    const exchange = createExchange({
      entity: () => entity,
      filters: () => undefined,
      publish: (state) => states.push(state),
      imported: () => undefined,
      sleep: clock.sleep,
    });
    const latest = () => states.at(-1);

    await exchange.actions.openFile(await bigFile(COOLDOWN_REQUEST_THRESHOLD));
    await exchange.actions.startImport();

    expect(latest()?.summary?.failed).toBeGreaterThan(0);
    expect(latest()?.cooldownSecondsLeft).toBe(COOLDOWN_MS / 1000);
  });
});
