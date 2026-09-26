import { beforeEach, describe, expect, it, vi } from "vitest";

import { createExchange } from "./exchange";
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
