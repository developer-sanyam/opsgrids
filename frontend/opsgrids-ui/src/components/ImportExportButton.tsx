/**
 * The one Import / Export button (ADR-0022). Presentational: it renders the state a data-exchange
 * hook reports and calls the actions it is given. It imports no HTTP and no spreadsheet code; the
 * app wires `useImportExport` from `@opsgrids/client` to it.
 *
 * The view types below repeat `@opsgrids/client`'s `DataExchange*` types field for field (the two
 * packages may not import each other). An app that passes one to the other fails its typecheck on
 * any drift.
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

/**
 * PLACEHOLDER (ADR-0022): renders nothing. The UI slice replaces this file with the real button
 * and dialog; the props above are the contract and do not change.
 */
export function ImportExportButton(props: ImportExportButtonProps) {
  void props;
  return null;
}
