/**
 * The words of the Import / Export dialog that depend on numbers: counts, titles, the sentence
 * that opens the result, and the text a screen reader is given while an import runs.
 *
 * Pure functions, so the plural and the "everything imported" cases are tested without a render.
 */

import type {
  ImportExportMeta,
  ImportExportPreview,
  ImportExportProgress,
  ImportExportSummary,
  ImportExportViewState,
} from "./importExportView";

const NUMBERS = new Intl.NumberFormat();

/** 1000 → "1,000" in the reader's own grouping. */
export function formatCount(value: number): string {
  return NUMBERS.format(value);
}

/** "1 row", "12 rows". */
export function rowsLabel(count: number): string {
  return `${formatCount(count)} ${count === 1 ? "row" : "rows"}`;
}

/** Whole percent, 0 to 100. An unknown total (0) is 0, not a division by zero. */
export function percentOf(done: number, total: number): number {
  if (total <= 0) return 0;
  return Math.min(100, Math.max(0, Math.floor((done / total) * 100)));
}

/**
 * Down to the tenth. A polite live region says whatever text it is given each time the text
 * changes, so an announcement built from a per-row count would read out every row; one built from
 * this changes ten times in a whole import.
 */
function tenths(percent: number): number {
  return Math.floor(percent / 10) * 10;
}

/** The heading of each phase: what focus lands on, and what a screen reader hears change. */
export function phaseTitle(state: ImportExportViewState, meta: ImportExportMeta): string {
  switch (state.phase) {
    case "menu":
      return `Import / Export ${meta.noun}`;
    case "reading":
      return "Reading your file";
    case "preview":
      return "Review your file";
    case "importing":
      return `Importing ${meta.noun}`;
    case "done":
      return state.summary?.cancelled === true ? "Import cancelled" : "Import finished";
    case "error":
      return "We couldn't read that file";
  }
}

function needsFixing(count: number): string {
  return `${formatCount(count)} ${count === 1 ? "needs" : "need"} fixing`;
}

/** What the preview says in one breath: what can be imported, and what cannot. */
export function previewSentence(preview: ImportExportPreview): string {
  if (preview.missingRequired.length > 0) {
    return "Your file is missing required columns, so nothing can be imported.";
  }
  if (preview.total === 0) return "No rows were found in this file.";
  const problems = preview.invalid === 0 ? "" : `, ${needsFixing(preview.invalid)}`;
  return `${rowsLabel(preview.valid)} ready to import${problems}.`;
}

function createdPhrase(created: number, meta: ImportExportMeta): string {
  if (created === 0) return `No ${meta.noun} were created`;
  if (created === 1) return `1 ${meta.nounSingular} was created`;
  return `${formatCount(created)} ${meta.noun} were created`;
}

/**
 * The first thing the result says, under a heading that already says finished or cancelled. The
 * clean run says everything imported, and only then.
 */
export function summarySentence(summary: ImportExportSummary, meta: ImportExportMeta): string {
  const notImported = summary.failed + summary.invalid;
  if (summary.cancelled) {
    const made = summary.created === 0 ? "Nothing was created." : `${createdPhrase(summary.created, meta)} before it stopped.`;
    return `${made} The remaining rows were not sent.`;
  }
  if (notImported > 0) {
    const rows = notImported === 1 ? "1 row was" : `${formatCount(notImported)} rows were`;
    return `${rows} not imported. ${createdPhrase(summary.created, meta)}.`;
  }
  if (summary.created > 0) {
    const skipped =
      summary.skipped === 0
        ? ""
        : ` ${formatCount(summary.skipped)} already existed and ${summary.skipped === 1 ? "was" : "were"} skipped.`;
    return `Everything imported. ${createdPhrase(summary.created, meta)}.${skipped}`;
  }
  if (summary.skipped > 0) {
    return "Nothing new to import. Every row already exists, so nothing was overwritten.";
  }
  return "No rows were sent.";
}

/**
 * The counts, spoken. The sentence on screen ("Everything imported. 6 companies were created.") is
 * not repeated here: a screen reader would read the same words twice, once as the announcement and
 * again as it reads the page. The heading the focus lands on already says finished or cancelled.
 */
function resultAnnouncement(summary: ImportExportSummary): string {
  return [
    `${formatCount(summary.created)} created`,
    `${formatCount(summary.skipped)} already existed`,
    `${formatCount(summary.failed)} failed`,
    `${formatCount(summary.invalid)} invalid`,
  ].join(", ") + ".";
}

function importingMessage(progress: ImportExportProgress | null): string {
  if (progress === null) return "Import started.";
  if (progress.waiting !== null) return "Waiting for the rate limit. The import continues by itself.";
  const step = tenths(percentOf(progress.done, progress.total));
  return step === 0 ? "Import started." : `Importing: ${step}% of rows sent.`;
}

function exportingMessage(exporting: ImportExportViewState["exporting"]): string {
  if (exporting === null) return "";
  if (exporting.total <= 0) return "Preparing the export.";
  const step = tenths(percentOf(exporting.fetched, exporting.total));
  return step === 0 ? "Export started." : `Exporting: ${step}% done.`;
}

/**
 * The one polite announcement for the dialog's current state. It moves in steps — a phase, a tenth
 * of the rows, the start of a rate-limit wait — never once per row, and never with the countdown
 * in it, so the region stays quiet while the numbers on screen keep counting.
 */
export function liveMessage(state: ImportExportViewState): string {
  switch (state.phase) {
    case "menu":
      return state.downloadingTemplate ? "Preparing the sample sheet." : exportingMessage(state.exporting);
    case "reading":
      return "Reading your file…";
    case "preview":
      return state.preview === null ? "" : previewSentence(state.preview);
    case "importing":
      return importingMessage(state.progress);
    case "done":
      return state.summary === null ? "" : resultAnnouncement(state.summary);
    // The error is an alert and announces itself; saying it here too would read it twice.
    case "error":
      return "";
  }
}
