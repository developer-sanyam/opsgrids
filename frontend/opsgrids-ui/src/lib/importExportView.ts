/**
 * What the Import / Export button (ADR-0022) takes: the view of a data-exchange session and the
 * actions on it. The component is presentational; an app wires `useImportExport` from
 * `@opsgrids/client` to it.
 *
 * These types repeat `@opsgrids/client`'s `DataExchange*` types field for field (the two packages
 * may not import each other). An app that passes one to the other fails its typecheck on any
 * drift. They live here, in a file with no imports, so the button and the phase views that render
 * them can all import them without importing each other in a circle;
 * `components/ImportExportButton.tsx` re-exports every one of them.
 */

/** What an import does with a row that names an existing record: skip it (`add`), or change it. */
export type ImportExportMode = "add" | "addAndUpdate";

export type ImportExportRowStatus = "created" | "updated" | "unchanged" | "skipped" | "failed" | "invalid";

export interface ImportExportRowOutcome {
  row: number;
  status: ImportExportRowStatus;
  message: string | null;
  id?: string | undefined;
  changes?: readonly string[] | undefined;
  /** An update row: which stored record it names, in words a person recognises. */
  record?: string | undefined;
  label: string;
}

export type ImportExportPhase = "menu" | "reading" | "preview" | "importing" | "done" | "error";

export type ImportExportPreviewAction = "create" | "update" | "unchanged" | "invalid" | "sample";

export interface ImportExportPreviewRow {
  row: number;
  values: Readonly<Record<string, string>>;
  error: string | null;
  sample: boolean;
  action: ImportExportPreviewAction;
  changes: readonly string[];
  /** A row that names a stored record: who it is, in words a person recognises. Null when it names none. */
  record: string | null;
  /** Something to say about a row that is not refused but does not do what it looks like. Null otherwise. */
  note: string | null;
}

/** What an update-mode preview did with the export baseline: `used`, `none` (the file has none) or
 *  `unusable` (it has one that was damaged, too big or another list's, and was ignored). */
export type ImportExportBaselineUse = "used" | "none" | "unusable";

export interface ImportExportPreview {
  fileName: string;
  mode: ImportExportMode;
  total: number;
  valid: number;
  invalid: number;
  sample: number;
  create: number;
  update: number;
  unchanged: number;
  columns: readonly { key: string; header: string }[];
  rows: readonly ImportExportPreviewRow[];
  unknownHeaders: readonly string[];
  missingRequired: readonly string[];
  matchWarning: string | null;
  baseline: ImportExportBaselineUse | null;
}

export interface ImportExportProgress {
  done: number;
  total: number;
  created: number;
  updated: number;
  skipped: number;
  failed: number;
  waiting: { reason: "rate-limit"; seconds: number } | null;
}

export interface ImportExportSummary {
  created: number;
  updated: number;
  unchanged: number;
  skipped: number;
  failed: number;
  invalid: number;
  cancelled: boolean;
  outcomes: readonly ImportExportRowOutcome[];
}

export interface ImportExportViewState {
  phase: ImportExportPhase;
  mode: ImportExportMode;
  error: string | null;
  preview: ImportExportPreview | null;
  progress: ImportExportProgress | null;
  summary: ImportExportSummary | null;
  downloadingTemplate: boolean;
  exporting: { fetched: number; total: number } | null;
  notice: string | null;
}

export interface ImportExportViewActions {
  setMode(mode: ImportExportMode): Promise<void>;
  openFile(file: File): Promise<void>;
  startImport(): Promise<void>;
  cancelImport(): void;
  reset(): void;
  downloadTemplate(): Promise<void>;
  exportAll(): Promise<void>;
  cancelExport(): void;
  downloadReport(): Promise<void>;
}

export interface ImportExportMeta {
  noun: string;
  nounSingular: string;
  maxRows: number;
  exportMaxRows: number;
  requiredHeaders: readonly string[];
  canExport: boolean;
  /** The list can be updated from a sheet: the dialog offers "Add new and update existing" to whoever may import. */
  canUpdate: boolean;
  /** The header of the column an export writes each record's id in ("Company ID"): the words the
   *  dialog's sentences use for it, so they say what the sheet says. */
  recordIdHeader: string;
}

export interface ImportExportButtonProps {
  meta: ImportExportMeta;
  state: ImportExportViewState;
  actions: ImportExportViewActions;
  /** The caller may create records here. Hides the sample sheet and Import, and with them the choice to
   *  update existing records, which needs no other permission in any list (a courtesy: the server decides). */
  canImport: boolean;
  /** The caller may read the list. Hides Export. */
  canExport: boolean;
}
