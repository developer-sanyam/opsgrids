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

export type ImportExportRowStatus = "created" | "skipped" | "failed" | "invalid";

export interface ImportExportRowOutcome {
  row: number;
  status: ImportExportRowStatus;
  message: string | null;
  id?: string | undefined;
  label: string;
}

export type ImportExportPhase = "menu" | "reading" | "preview" | "importing" | "done" | "error";

export interface ImportExportPreviewRow {
  row: number;
  values: Readonly<Record<string, string>>;
  error: string | null;
  sample: boolean;
}

export interface ImportExportPreview {
  fileName: string;
  total: number;
  valid: number;
  invalid: number;
  sample: number;
  columns: readonly { key: string; header: string }[];
  rows: readonly ImportExportPreviewRow[];
  unknownHeaders: readonly string[];
  missingRequired: readonly string[];
}

export interface ImportExportProgress {
  done: number;
  total: number;
  created: number;
  skipped: number;
  failed: number;
  waiting: { reason: "rate-limit"; seconds: number } | null;
}

export interface ImportExportSummary {
  created: number;
  skipped: number;
  failed: number;
  invalid: number;
  cancelled: boolean;
  outcomes: readonly ImportExportRowOutcome[];
}

export interface ImportExportViewState {
  phase: ImportExportPhase;
  error: string | null;
  preview: ImportExportPreview | null;
  progress: ImportExportProgress | null;
  summary: ImportExportSummary | null;
  downloadingTemplate: boolean;
  exporting: { fetched: number; total: number } | null;
  notice: string | null;
}

export interface ImportExportViewActions {
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
}

export interface ImportExportButtonProps {
  meta: ImportExportMeta;
  state: ImportExportViewState;
  actions: ImportExportViewActions;
  /** The caller may create records here. Hides the sample sheet and Import (a courtesy: the server decides). */
  canImport: boolean;
  /** The caller may read the list. Hides Export. */
  canExport: boolean;
}
