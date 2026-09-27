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
import { WorkbookReadError, readWorkbook } from "./workbook";
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
};

const EXPORT_PAGE_SIZE = 200;
export const DEFAULT_EXPORT_MAX_ROWS = 10_000;

export interface ExchangeHost {
  entity(): AnyEntity;
  /** The screen's current list filters, read when an export starts. */
  filters(): unknown;
  publish(state: DataExchangeState): void;
  /** An import run ended having created or updated at least one record; the screen may reload its list. */
  imported(summary: ImportSummary): void;
}

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
  /**
   * The stored records an update-mode preview matches rows against, loaded like the lookups: once
   * per dialog session, one load in flight shared by everyone who asks, a failed load not kept.
   * Dropped when the dialog is reset or disposed and after an import that created or changed
   * records (the list is no longer what was read). `settled` says a mode change can be answered
   * without a wait.
   */
  let existingLoad: { loader: unknown; loading: Promise<readonly unknown[]>; settled: readonly unknown[] | null } | null =
    null;

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
        settled: null,
      };
      existingLoad = entry;
      entry.loading.then(
        (items) => {
          entry.settled = items;
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
    existingLoad !== null && existingLoad.loader === spec.loadExisting && existingLoad.settled !== null;

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
    publish(spec !== undefined && !existingIsHere(spec) ? { mode, phase: "reading", preview: null } : { mode });
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

  const startImport = async (): Promise<void> => {
    const { preview } = current;
    if (importing || current.phase !== "preview" || preview === null || prepared === null) return;
    if (preview.missingRequired.length > 0) return;
    const run = prepared;
    const entity = host.entity();
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
      });
      finished = summarise(run, result);
      publish({ phase: "done", progress: null, summary: finished });
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
