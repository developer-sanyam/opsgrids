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
import { prepareImport } from "./preview";
import type { PreparedImport } from "./preview";
import { buildReportWorkbook, reportFileName } from "./report";
import { runImport } from "./runner";
import type { RunResult } from "./runner";
import { buildSampleWorkbook, sampleFileName } from "./template";
import type {
  DataExchangeActions,
  DataExchangeState,
  ImportProgress,
  ImportSummary,
  LookupSet,
} from "./types";
import { WorkbookReadError, readWorkbook } from "./workbook";

export const IDLE_STATE: DataExchangeState = {
  phase: "menu",
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
  /** An import run ended having created at least one record; the screen may reload its list. */
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
  if (error instanceof WorkbookReadError) return error.message;
  if (error instanceof ApiError) {
    return `The lists this file is checked against could not be loaded. ${problemMessage(error)}`;
  }
  return READ_FAILED;
}

/** "The sample sheet could not be created. <the server's sentence, or Try again.>" */
function failureNotice(what: string, error: unknown): string {
  return `${what} ${error instanceof ApiError ? problemMessage(error) : "Try again."}`;
}

async function loadLookups(entity: AnyEntity, signal: AbortSignal): Promise<LookupSet> {
  return entity.loadLookups === undefined ? {} : entity.loadLookups(signal);
}

function summarise(prepared: PreparedImport, result: RunResult): ImportSummary {
  return {
    created: result.created,
    skipped: result.skipped,
    failed: result.failed,
    invalid: prepared.invalid.length + result.invalid,
    cancelled: result.cancelled,
    outcomes: [...prepared.invalid, ...result.outcomes].sort((a, b) => a.row - b.row),
  };
}

/**
 * Drops a row the list returned twice. Offset paging over an ordering that is not unique can hand
 * back one row on two pages (and skip another); the `id` column, when the entity has one, is what
 * tells two rows apart.
 */
function withoutRepeats(entity: AnyEntity, items: readonly unknown[], lookups: LookupSet): unknown[] {
  const source = entity.export;
  if (source === undefined || !entity.columns.some((column) => column.key === "id")) return [...items];
  const seen = new Set<string>();
  return items.filter((item) => {
    const id = source.toRow(item, lookups).id;
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
  let importing = false;
  let cancelRequested = false;
  let exportRun: AbortController | null = null;
  let reporting = false;

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
    publish({ phase: "reading", error: null, preview: null, progress: null, summary: null, notice: null });
    try {
      const sheet = await readWorkbook(file, entity);
      const lookups = await loadLookups(entity, signal);
      if (mine !== session || signal.aborted) return;
      const built = prepareImport(entity, sheet, { lookups, signal }, file.name);
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
    // Outside the try: a screen's reload that throws is its own bug, not a failed import.
    if (finished !== null && finished.created > 0 && attached) host.imported(finished);
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
    publish({ phase: "menu", error: null, preview: null, progress: null, summary: null, notice: null });
  };

  const downloadTemplate = async (): Promise<void> => {
    if (current.downloadingTemplate) return;
    const signal = life.signal;
    const entity = host.entity();
    const mine = session;
    publish({ downloadingTemplate: true, notice: null });
    try {
      const lookups = await loadLookups(entity, signal);
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
      const lookups = await loadLookups(entity, run.signal);
      const fetched: unknown[] = [];
      let total = 0;
      for (let page = 1; ; page += 1) {
        if (!live()) return;
        const result = await source.fetchPage({ page, size: EXPORT_PAGE_SIZE, filters, signal: run.signal });
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
    actions: { openFile, startImport, cancelImport, reset, downloadTemplate, exportAll, cancelExport, downloadReport },
    attach() {
      if (attached) return;
      attached = true;
      life = new AbortController();
      cancelRequested = false;
      host.publish(current);
    },
    dispose() {
      attached = false;
      life.abort();
      cancelRequested = true;
      exportRun?.abort();
    },
  };
}
