/**
 * The words of the Import / Export dialog that depend on numbers: counts, titles, the sentence
 * that opens the result, and the text a screen reader is given while an import runs.
 *
 * Pure functions, so the plural and the "everything imported" cases are tested without a render.
 */

import type {
  ImportExportMeta,
  ImportExportMode,
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
  if (preview.mode === "addAndUpdate" && (preview.valid > 0 || preview.unchanged > 0)) {
    return updatePreviewSentence(preview);
  }
  const problems = preview.invalid === 0 ? "" : `, ${needsFixing(preview.invalid)}`;
  return `${rowsLabel(preview.valid)} ready to import${problems}.`;
}

/** The same breath for update mode, where a row may change a record, add one, or change nothing. */
function updatePreviewSentence(preview: ImportExportPreview): string {
  const rest = [
    preview.unchanged > 0 ? `${formatCount(preview.unchanged)} unchanged` : null,
    preview.invalid > 0 ? needsFixing(preview.invalid) : null,
  ].filter((part) => part !== null);
  if (preview.valid > 0) {
    const kinds = [
      preview.update > 0 ? `${formatCount(preview.update)} to update` : null,
      preview.create > 0 ? `${formatCount(preview.create)} new` : null,
    ].filter((part) => part !== null);
    const tail = rest.length === 0 ? "" : `, ${rest.join(", ")}`;
    return `${rowsLabel(preview.valid)} ready to import (${kinds.join(", ")})${tail}.`;
  }
  if (preview.invalid === 0) return "Nothing to change: every row already matches its record.";
  return `No rows to send: ${rest.join(", ")}.`;
}

/** The import button's words: the rows it will send, or, in update mode with none, that there is nothing to change. */
export function importButtonLabel(preview: ImportExportPreview): string {
  if (preview.mode === "addAndUpdate" && preview.valid === 0) return "Nothing to change";
  return `Import ${rowsLabel(preview.valid)}`;
}

function createdPhrase(created: number, meta: ImportExportMeta): string {
  if (created === 0) return `No ${meta.noun} were created`;
  if (created === 1) return `1 ${meta.nounSingular} was created`;
  return `${formatCount(created)} ${meta.noun} were created`;
}

/** "3 updated, 2 created, 5 unchanged, 1 failed": what became of the rows of an update run, leaving out what none had. */
function updateCounts(summary: ImportExportSummary, includeFailed: boolean): string {
  return [
    summary.updated > 0 ? `${formatCount(summary.updated)} updated` : null,
    summary.created > 0 ? `${formatCount(summary.created)} created` : null,
    summary.unchanged > 0 ? `${formatCount(summary.unchanged)} unchanged` : null,
    includeFailed && summary.failed + summary.invalid > 0
      ? `${formatCount(summary.failed + summary.invalid)} failed`
      : null,
  ]
    .filter((part) => part !== null)
    .join(", ");
}

/** A run that updated records, or found some that already matched: said in counts, not as "created". */
function updateSentence(summary: ImportExportSummary): string {
  const notImported = summary.failed + summary.invalid;
  if (summary.cancelled) {
    const saved = updateCounts({ ...summary, unchanged: 0 }, false);
    return `${saved === "" ? "Nothing was changed." : `${saved} before it stopped.`} The remaining rows were not sent.`;
  }
  if (notImported > 0) return `${updateCounts(summary, true)}.`;
  if (summary.updated + summary.created > 0) return `Everything imported. ${updateCounts(summary, false)}.`;
  return "Nothing to change. Every row already matches its record, so nothing was sent.";
}

/**
 * The first thing the result says, under a heading that already says finished or cancelled. The
 * clean run says everything imported, and only then. A run that updated records or found some that
 * already matched says what became of every kind of row instead.
 */
export function summarySentence(summary: ImportExportSummary, meta: ImportExportMeta): string {
  if (summary.updated > 0 || summary.unchanged > 0) return updateSentence(summary);
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
function resultAnnouncement(summary: ImportExportSummary, mode: ImportExportMode): string {
  // Updating never skips a row that exists (a 409 is a failure there), so it has no "already existed".
  const counts =
    mode === "addAndUpdate"
      ? [
          `${formatCount(summary.created)} created`,
          `${formatCount(summary.updated)} updated`,
          `${formatCount(summary.unchanged)} unchanged`,
        ]
      : [`${formatCount(summary.created)} created`, `${formatCount(summary.skipped)} already existed`];
  return `${[...counts, `${formatCount(summary.failed)} failed`, `${formatCount(summary.invalid)} invalid`].join(", ")}.`;
}

/**
 * What "Add new and update existing" does, in plain words: how a row finds its record, what a blank
 * cell and `[clear]` mean, and what the update cannot protect against. Shown under the choice.
 */
export const UPDATE_RULES: readonly string[] = [
  "Start from an export of the list: its Record ID column names each record.",
  "A row is matched to a record by its Record ID or by the list's unique key, such as a SKU or a code. A row that matches nothing is added as a new record.",
  "A blank cell leaves the field as it is. Only the cells you fill in are compared with the record.",
  "Type [clear] in a cell to empty a field. Not every field can be emptied.",
  "Only fields that changed are sent. A row that changes nothing sends nothing.",
  "The update cannot detect edits made by someone else after your file is read. Their changes to the same field would be overwritten.",
  "Records you cannot see are not matched and will be added as new; adding one that already exists will fail as a duplicate.",
];

/** What the reading phase is doing: in update mode it also loads and compares against every stored record. */
export function readingSentence(mode: ImportExportMode): string {
  return mode === "addAndUpdate"
    ? "Loading the current records and comparing every row with them. A large list takes a few seconds."
    : "Checking the columns and every row. A large file takes a few seconds.";
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
      return state.summary === null ? "" : resultAnnouncement(state.summary, state.mode);
    // The error is an alert and announces itself; saying it here too would read it twice.
    case "error":
      return "";
  }
}
