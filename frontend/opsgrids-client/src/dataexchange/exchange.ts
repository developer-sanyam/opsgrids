/**
 * The state machine behind the Import / Export dialog, free of React so that its guards (no double
 * submit, a stale read never overwrites a newer one) are plain synchronous variables rather than
 * stale closures. `useImportExport` owns one of these and mirrors its state into React.
 *
 * Nothing here throws to the caller: a failure becomes `phase: "error"` (a file that cannot be
 * imported) or `notice` (a template or export that failed), each a sentence for the user.
 */

import { ApiError } from "../http";
import { problemMessage } from "../problem";
import type { AnyEntity } from "./cells";
import { downloadBlob } from "./download";
import { exportFileName, buildExportWorkbook } from "./exporter";
import { ReferenceListError, pagedRequest } from "./lookups";
import { prepareImport } from "./preview";
import type { PreparedImport } from "./preview";
import { buildReportWorkbook, reportFileName } from "./report";
import { runImport } from "./runner";
import type { RunResult } from "./runner";
import { buildSampleWorkbook, sampleFileName } from "./template";
import type {
  DataExchangeActions,
  DataExchangeState,
  ImportMode,
  ImportProgress,
  ImportSummary,
  LookupSet,
  UpdateSpec,
} from "./types";
import { DEFAULT_EXPORT_MAX_ROWS, WorkbookReadError, readWorkbook } from "./workbook";
import type { ParsedSheet } from "./workbook";

export const IDLE_STATE: DataExchangeState = {
  phase: "menu",
  mode: "add",
  error: null,
  preview: null,
  progress: null,
  summary: null,
  downloadingTemplate: false,
  exporting: null,
  notice: null,
  cooldownSecondsLeft: null,
};

const EXPORT_PAGE_SIZE = 200;

export interface ExchangeHost {
  entity(): AnyEntity;
  /** The screen's current list filters, read when an export starts. */
  filters(): unknown;
  publish(state: DataExchangeState): void;
  /** An import run ended having created or updated at least one record; the screen may reload its list. */
  imported(summary: ImportSummary): void;
  /** The clock, in milliseconds. Default `Date.now`; a test moves it. */
  now?: (() => number) | undefined;
  /** The cooldown's own countdown tick. Default a real `setTimeout`; a test resolves at once. */
  sleep?: ((ms: number) => Promise<void>) | undefined;
}

/**
 * A run that sent this many requests or more (created + updated + skipped + failed: every row
 * that actually reached the server, never `unchanged` or `invalid`) opens a cooldown before
 * another one can start. The row cap (`workbook.ts`, `DEFAULT_MAX_ROWS`) exists for the browser's
 * sake; this is what keeps a large file from taking every one of the account's shared 300
 * requests a minute back to back, run after run, leaving nothing for anything else the person or
 * a teammate does meanwhile. It never blocks reviewing a file, only sending one.
 */
export const COOLDOWN_REQUEST_THRESHOLD = 300;
/** A full minute of the gateway's per-user budget: enough for whatever this run used to clear. */
export const COOLDOWN_MS = 60_000;

const realSleep = (ms: number): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * How old the stored records an update was previewed against may be when Import is pressed. Past
 * this the records are read again and the file planned again: someone may have changed them while
 * the preview was open, and an update sends what the preview said it would.
 */
export const SNAPSHOT_MAX_AGE_MS = 2 * 60_000;

export const RECORDS_CHANGED_NOTICE =
  "The records changed while you were reviewing this file, so the preview has been refreshed and nothing was imported. Check it, then choose Import again.";

export interface Exchange {
  actions: DataExchangeActions;
  /** Arms the exchange again after `dispose`: React StrictMode mounts, unmounts and mounts once more. */
  attach(): void;
  /** Aborts everything in flight and stops publishing. */
  dispose(): void;
}

const READ_FAILED = "That file could not be read. Check that it is an Excel workbook (.xlsx) and try again.";

function readFailure(error: unknown): string {
  if (error instanceof WorkbookReadError || error instanceof ReferenceListError) return error.message;
  if (error instanceof ApiError) {
    return `The lists this file is checked against could not be loaded. ${problemMessage(error)}`;
  }
  return READ_FAILED;
}

/** "The sample sheet could not be created. <the server's sentence, or Try again.>" */
function failureNotice(what: string, error: unknown): string {
  if (error instanceof ReferenceListError) return `${what} ${error.message}`;
  return `${what} ${error instanceof ApiError ? problemMessage(error) : "Try again."}`;
}

/** Waits for `promise`, but only as long as `signal` allows; the promise itself carries on for whoever else needs it. */
function untilAborted<T>(promise: Promise<T>, signal: AbortSignal): Promise<T> {
  const cancelled = (): DOMException => new DOMException("The request was cancelled", "AbortError");
  if (signal.aborted) return Promise.reject(cancelled());
  return new Promise<T>((resolve, reject) => {
    const onAbort = (): void => reject(cancelled());
    signal.addEventListener("abort", onAbort, { once: true });
    promise.then(
      (value) => {
        signal.removeEventListener("abort", onAbort);
        resolve(value);
      },
      (error: unknown) => {
        signal.removeEventListener("abort", onAbort);
        reject(error);
      },
    );
  });
}

function summarise(prepared: PreparedImport, result: RunResult): ImportSummary {
  return {
    created: result.created,
    updated: result.updated,
    unchanged: prepared.unchanged.length + result.unchanged,
    skipped: result.skipped,
    failed: result.failed,
    invalid: prepared.invalid.length + result.invalid,
    cancelled: result.cancelled,
    outcomes: [...prepared.invalid, ...prepared.unchanged, ...result.outcomes].sort((a, b) => a.row - b.row),
  };
}

/**
 * Drops a row the list returned twice. Offset paging over an ordering that is not unique can hand
 * back one row on two pages (and skip another); the record id column (`recordIdKey`, else a column
 * keyed `id`), when the entity has one, is what tells two rows apart.
 */
function withoutRepeats(entity: AnyEntity, items: readonly unknown[], lookups: LookupSet): unknown[] {
  const source = entity.export;
  const idKey = entity.recordIdKey ?? "id";
  if (source === undefined || !entity.columns.some((column) => column.key === idKey)) return [...items];
  const seen = new Set<string>();
  return items.filter((item) => {
    const id = source.toRow(item, lookups)[idKey];
    if (id === null || id === undefined) return true;
    const key = String(id);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

const count = new Intl.NumberFormat("en");

export function createExchange(host: ExchangeHost): Exchange {
  let current = IDLE_STATE;
  let attached = true;
  let life = new AbortController();
  /** Bumped by every `openFile` and `reset`; a read that finds it changed drops its result. */
  let session = 0;
  let prepared: PreparedImport | null = null;
  /** The cells of the file being reviewed, kept so that a change of mode checks them again without reading the file again. */
  let sheetRead: { sheet: ParsedSheet; fileName: string } | null = null;
  let importing = false;
  let cancelRequested = false;
  let exportRun: AbortController | null = null;
  let reporting = false;
  /**
   * The reference lists, loaded once per dialog session and shared by every action in it: the
   * pages of a large organisation's lists cost real requests from a shared budget, and opening a
   * file, downloading the sample sheet and exporting would each ask for all of them again.
   * Concurrent actions share one load in flight. Dropped when the dialog is reset or disposed and
   * after an import that created records (the lists have changed); a failed load is not kept.
   */
  let lookupLoad: { loader: unknown; loading: Promise<LookupSet> } | null = null;
  /** Set while Import reads the records again before sending, so a second press cannot start a second read. */
  let revalidating = false;
  const now = (): number => (host.now ?? Date.now)();
  const sleep = host.sleep ?? realSleep;
  /** Bumped every time a cooldown starts, so a stale tick loop from an earlier one (or from before
   *  a reset) knows to stop publishing rather than fight a newer one, or one that no longer applies. */
  let cooldownEpoch = 0;

  /** Counts a run's `cooldownSecondsLeft` down to zero, one publish a second, then clears it.
   *  Not awaited by its caller: it outlives `startImport`, the way `waitOutRateLimit` outlives one
   *  request inside a run. */
  const runCooldown = async (durationMs: number): Promise<void> => {
    cooldownEpoch += 1;
    const mine = cooldownEpoch;
    let remaining = durationMs;
    while (remaining > 0) {
      publish({ cooldownSecondsLeft: Math.ceil(remaining / 1000) });
      const step = Math.min(1000, remaining);
      await sleep(step);
      if (mine !== cooldownEpoch || !attached) return;
      remaining -= step;
    }
    publish({ cooldownSecondsLeft: null });
  };
  /**
   * The stored records an update-mode preview matches rows against: one load in flight shared by
   * everyone who asks, a failed load not kept. A mode change is answered from a load that has
   * settled (`settledAt`) without a wait, but a file opened after that is compared
   * with a fresh read, since the records may have changed while the last file was being edited, and
   * Import reads again once the records are older than `SNAPSHOT_MAX_AGE_MS`. Dropped when the dialog
   * is reset or disposed and after an import that created or changed records.
   */
  let existingLoad: {
    loader: unknown;
    loading: Promise<readonly unknown[]>;
    /** When the load finished, or null while it is still in flight. */
    settledAt: number | null;
  } | null = null;

  const lookupsFor = (entity: AnyEntity, signal: AbortSignal): Promise<LookupSet> => {
    const loader = entity.loadLookups;
    if (loader === undefined) return Promise.resolve({});
    if (lookupLoad === null || lookupLoad.loader !== loader) {
      // Tied to the dialog's life, not to the action that happened to ask first: cancelling an
      // export must not fail the file being read on the same load.
      const entry = { loader, loading: loader(life.signal) };
      lookupLoad = entry;
      entry.loading.catch(() => {
        if (lookupLoad === entry) lookupLoad = null;
      });
    }
    return untilAborted(lookupLoad.loading, signal);
  };

  const existingFor = (spec: UpdateSpec<unknown>, lookups: LookupSet, signal: AbortSignal): Promise<readonly unknown[]> => {
    if (existingLoad === null || existingLoad.loader !== spec.loadExisting) {
      const entry: NonNullable<typeof existingLoad> = {
        loader: spec.loadExisting,
        loading: spec.loadExisting({ lookups, signal: life.signal }),
        settledAt: null,
      };
      existingLoad = entry;
      entry.loading.then(
        () => {
          entry.settledAt = now();
        },
        () => {
          if (existingLoad === entry) existingLoad = null;
        },
      );
    }
    return untilAborted(existingLoad.loading, signal);
  };

  /** Whether the records to update are already here, so that a change of mode needs no wait. */
  const existingIsHere = (spec: UpdateSpec<unknown>): boolean =>
    existingLoad !== null && existingLoad.loader === spec.loadExisting && existingLoad.settledAt !== null;

  /** Checks a read sheet in the current mode: against the stored records in update mode, else on its own. */
  const previewOf = async (entity: AnyEntity, read: { sheet: ParsedSheet; fileName: string }, signal: AbortSignal) => {
    const lookups = await lookupsFor(entity, signal);
    // Read now and not before the wait: a change of mode while the lists load is honoured.
    const mode = current.mode;
    const spec = mode === "addAndUpdate" ? entity.update : undefined;
    const existing = spec === undefined ? null : await existingFor(spec, lookups, signal);
    return prepareImport(entity, read.sheet, { lookups, signal }, read.fileName, mode, existing);
  };

  const publish = (patch: Partial<DataExchangeState>): void => {
    current = { ...current, ...patch };
    if (attached) host.publish(current);
  };

  const openFile = async (file: File): Promise<void> => {
    if (importing) return;
    session += 1;
    const mine = session;
    const signal = life.signal;
    const entity = host.entity();
    prepared = null;
    sheetRead = null;
    // A file chosen now is compared with the records as they are now, not as they were when an
    // earlier file was opened: that one may have been edited in Excel for an hour since. A load still
    // in flight began after the dialog opened, so it is as fresh as a new one and is shared.
    if (existingLoad !== null && existingLoad.settledAt !== null) existingLoad = null;
    publish({ phase: "reading", error: null, preview: null, progress: null, summary: null, notice: null });
    try {
      const read = { sheet: await readWorkbook(file, entity), fileName: file.name };
      const built = await previewOf(entity, read, signal);
      if (mine !== session || signal.aborted) return;
      sheetRead = read;
      prepared = built.prepared;
      publish({ phase: "preview", preview: built.preview });
    } catch (error) {
      if (mine !== session || signal.aborted) return;
      publish({ phase: "error", error: readFailure(error) });
    }
  };

  const setMode = async (mode: ImportMode): Promise<void> => {
    if (importing || mode === current.mode) return;
    // A list that has no update spec has nothing to switch to.
    if (mode === "addAndUpdate" && host.entity().update === undefined) return;
    if (current.phase === "menu") {
      publish({ mode });
      return;
    }
    // While a file is being read the mode it is read in is already fixed; after the result there is nothing to redo.
    const read = sheetRead;
    if (current.phase !== "preview" || read === null) return;
    const entity = host.entity();
    const signal = life.signal;
    session += 1;
    const mine = session;
    const spec = mode === "addAndUpdate" ? entity.update : undefined;
    // What was previewed is for the other mode: it must not be sent while the new one is worked out.
    prepared = null;
    // Only a wait that is really a wait hides the preview; a mode whose records are already loaded
    // answers at once and leaves whatever has focus where it is.
    publish(
      spec !== undefined && !existingIsHere(spec)
        ? { mode, phase: "reading", preview: null, notice: null }
        : { mode, notice: null },
    );
    try {
      const built = await previewOf(entity, read, signal);
      if (mine !== session || signal.aborted) return;
      prepared = built.prepared;
      publish({ phase: "preview", preview: built.preview });
    } catch (error) {
      if (mine !== session || signal.aborted) return;
      publish({ phase: "error", error: readFailure(error) });
    }
  };

  /** Whether an update was previewed against stored records older than `SNAPSHOT_MAX_AGE_MS`. */
  const snapshotIsStale = (entity: AnyEntity): boolean => {
    const settledAt = existingLoad?.settledAt;
    return (
      current.mode === "addAndUpdate" &&
      entity.update !== undefined &&
      sheetRead !== null &&
      settledAt !== undefined &&
      settledAt !== null &&
      now() - settledAt > SNAPSHOT_MAX_AGE_MS
    );
  };

  /**
   * Before an update is sent on records that have gone stale: reads them again and plans the same
   * file again. When every row would still do what the user reviewed, the fresh plan is the one to
   * send (true). When any row's action or changes differ, the new preview replaces the old one with
   * a notice and nothing is sent (false): what would be written is no longer what was approved. A
   * read that fails leaves the review as it was, with the reason, and sends nothing.
   */
  const stillAsReviewed = async (entity: AnyEntity, reviewed: PreparedImport): Promise<boolean> => {
    const read = sheetRead;
    if (read === null) return false;
    const shown = current.preview;
    const stale = existingLoad;
    const signal = life.signal;
    session += 1;
    const mine = session;
    revalidating = true;
    // Nothing may be sent from the old plan while the new one is worked out.
    prepared = null;
    existingLoad = null;
    publish({ phase: "reading", preview: null, notice: null });
    try {
      const built = await previewOf(entity, read, signal);
      if (mine !== session || signal.aborted) return false;
      prepared = built.prepared;
      if (built.prepared.review === reviewed.review) {
        publish({ phase: "preview", preview: built.preview });
        return true;
      }
      publish({ phase: "preview", preview: built.preview, notice: RECORDS_CHANGED_NOTICE });
      return false;
    } catch (error) {
      if (mine !== session || signal.aborted) return false;
      // The review stands, still marked as stale, so pressing Import again tries the read again.
      prepared = reviewed;
      existingLoad = stale;
      publish({
        phase: "preview",
        preview: shown,
        notice: `Nothing was imported, because the current records could not be checked. ${readFailure(error)}`,
      });
      return false;
    } finally {
      revalidating = false;
    }
  };

  const startImport = async (): Promise<void> => {
    const first = current.preview;
    if (importing || revalidating || current.phase !== "preview" || first === null || prepared === null) return;
    if (first.missingRequired.length > 0) return;
    if (current.cooldownSecondsLeft !== null) return;
    const entity = host.entity();
    // Only a stale snapshot is worth a wait; the guard below is synchronous otherwise, so a second
    // press cannot slip in before `importing` is set.
    if (snapshotIsStale(entity) && !(await stillAsReviewed(entity, prepared))) return;
    // The plan and the preview it belongs to, as they are after the check: the fresh ones when it read again.
    const { preview } = current;
    const run = prepared;
    if (current.phase !== "preview" || preview === null || run === null) return;
    const signal = life.signal;
    importing = true;
    cancelRequested = false;
    const initial: ImportProgress = {
      done: 0,
      total: run.valid.length,
      created: 0,
      updated: 0,
      skipped: 0,
      failed: 0,
      waiting: null,
    };
    publish({ phase: "importing", progress: initial, summary: null, notice: null });
    let finished: ImportSummary | null = null;
    try {
      const result = await runImport({
        entity,
        rows: run.valid,
        mode: preview.mode,
        ctx: { lookups: run.lookups, signal },
        onProgress: (progress) => publish({ progress }),
        isCancelled: () => cancelRequested || signal.aborted,
        now: host.now,
        sleep: host.sleep,
      });
      finished = summarise(run, result);
      publish({ phase: "done", progress: null, summary: finished });
      const sent = finished.created + finished.updated + finished.skipped + finished.failed;
      if (sent >= COOLDOWN_REQUEST_THRESHOLD) void runCooldown(COOLDOWN_MS);
    } catch {
      publish({
        phase: "error",
        progress: null,
        error: "The import stopped unexpectedly. Some rows may already have been created; check the list before trying again.",
      });
    } finally {
      importing = false;
    }
    if (finished !== null && finished.created + finished.updated > 0) {
      // Rows now exist, or read differently, that the lists did not know about (a manager, a
      // company, a renamed record): ask again next time.
      lookupLoad = null;
      existingLoad = null;
      // Outside the try: a screen's reload that throws is its own bug, not a failed import.
      if (attached) host.imported(finished);
    }
  };

  const cancelImport = (): void => {
    if (importing) cancelRequested = true;
  };

  const reset = (): void => {
    // A run in flight cannot be discarded (its rows are already on the server), so it is stopped
    // and finishes into `done` with its summary; the caller resets again from there.
    if (importing) {
      cancelRequested = true;
      return;
    }
    session += 1;
    prepared = null;
    sheetRead = null;
    lookupLoad = null;
    existingLoad = null;
    // Back to adding: a mode that overwrites records is chosen each time, never inherited.
    publish({ phase: "menu", mode: "add", error: null, preview: null, progress: null, summary: null, notice: null });
  };

  const downloadTemplate = async (): Promise<void> => {
    if (current.downloadingTemplate) return;
    const signal = life.signal;
    const entity = host.entity();
    const mine = session;
    publish({ downloadingTemplate: true, notice: null });
    try {
      const lookups = await lookupsFor(entity, signal);
      const blob = await buildSampleWorkbook(entity, lookups);
      if (!signal.aborted) downloadBlob(sampleFileName(entity), blob);
    } catch (error) {
      // A failure that lands after the dialog was reset would greet the next opening with a stale error.
      if (!signal.aborted && mine === session) {
        publish({ notice: failureNotice("The sample sheet could not be created.", error) });
      }
    } finally {
      publish({ downloadingTemplate: false });
    }
  };

  const exportAll = async (): Promise<void> => {
    const entity = host.entity();
    const source = entity.export;
    if (source === undefined || current.exporting !== null) return;
    const run = new AbortController();
    exportRun = run;
    const mine = session;
    /** False once cancelled or superseded: a run that lost its place stays silent. */
    const live = (): boolean => exportRun === run && !run.signal.aborted;
    const filters = host.filters();
    const cap = source.maxRows ?? DEFAULT_EXPORT_MAX_ROWS;
    publish({ exporting: { fetched: 0, total: 0 }, notice: null });
    try {
      const lookups = await lookupsFor(entity, run.signal);
      const fetched: unknown[] = [];
      let total = 0;
      for (let page = 1; ; page += 1) {
        if (!live()) return;
        // Through the same per-minute budget as the reference lists, waiting out a 429 once
        // instead of failing a long export half way.
        const result = await pagedRequest(
          () => source.fetchPage({ page, size: EXPORT_PAGE_SIZE, filters, signal: run.signal }),
          run.signal,
        );
        if (!live()) return;
        total = result.total;
        fetched.push(...result.items);
        publish({ exporting: { fetched: Math.min(fetched.length, cap), total: Math.min(total, cap) } });
        if (fetched.length >= cap || page >= result.pages || result.items.length === 0) break;
      }
      const items = withoutRepeats(entity, fetched, lookups).slice(0, cap);
      const blob = await buildExportWorkbook(entity, items, lookups);
      if (!live()) return;
      downloadBlob(exportFileName(entity, new Date()), blob);
      const expected = Math.min(total, cap);
      let notice: string | null = null;
      if (items.length < expected) {
        // Offset paging over an ordering that is not unique can skip a row and repeat another.
        notice = `Exported ${count.format(items.length)} of ${count.format(expected)} rows. The list changed or paged unevenly while exporting: run the export again, or narrow the filters.`;
      } else if (total > cap) {
        notice = `Only the first ${count.format(cap)} of ${count.format(total)} ${entity.noun} were exported. Narrow the filters to export the rest.`;
      }
      if (notice !== null && mine === session) publish({ notice });
    } catch (error) {
      if (live() && mine === session) {
        publish({ notice: failureNotice(`The ${entity.noun} could not be exported.`, error) });
      }
    } finally {
      if (exportRun === run) {
        exportRun = null;
        publish({ exporting: null });
      }
    }
  };

  const cancelExport = (): void => {
    if (exportRun === null) return;
    exportRun.abort();
    exportRun = null;
    publish({ exporting: null });
  };

  const downloadReport = async (): Promise<void> => {
    const { summary } = current;
    if (summary === null || reporting) return;
    const entity = host.entity();
    reporting = true;
    publish({ notice: null });
    try {
      const blob = await buildReportWorkbook(entity, summary);
      if (!life.signal.aborted) downloadBlob(reportFileName(entity), blob);
    } catch {
      publish({ notice: "The result file could not be created. Try again." });
    } finally {
      reporting = false;
    }
  };

  return {
    actions: {
      setMode,
      openFile,
      startImport,
      cancelImport,
      reset,
      downloadTemplate,
      exportAll,
      cancelExport,
      downloadReport,
    },
    attach() {
      if (attached) return;
      attached = true;
      life = new AbortController();
      cancelRequested = false;
      host.publish(current);
    },
    dispose() {
      attached = false;
      lookupLoad = null;
      existingLoad = null;
      life.abort();
      cancelRequested = true;
      exportRun?.abort();
    },
  };
}
