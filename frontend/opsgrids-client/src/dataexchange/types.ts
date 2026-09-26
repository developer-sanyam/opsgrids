/**
 * The contract of the Excel import/export engine (ADR-0022). Every product describes each
 * importable list once, as an `ImportEntity`; the engine turns that description into the sample
 * sheet, the importer, the exporter and the result report, and `useImportExport` drives them.
 *
 * `@opsgrids/ui`'s `ImportExportButton` takes the hook's `state`, `actions` and `meta` as props.
 * The two packages may not import each other, so the view types there
 * (`ImportExportViewState`, `ImportExportViewActions`, `ImportExportMeta`) repeat the shapes
 * below field for field; an app that wires one to the other fails its typecheck on any drift.
 */

/** What one cell holds after reading, before its column coerces it. */
export type SheetValue = string | number | boolean | Date | null;

/** What a column's value is once coerced and validated. Dates are `YYYY-MM-DD`, money is integer
 *  minor units, an enum is the option's `value`, an email is lower-cased, a phone keeps `+` and
 *  digits only. */
export type ParsedValue = string | number | boolean | null;

/** One row after coercion, keyed by `ImportColumn.key`. Blank optional cells are `null`. */
export type ParsedRow = Readonly<Record<string, ParsedValue>>;

export type ColumnKind =
  | "text"
  | "integer"
  | "decimal"
  | "money"
  | "date"
  | "boolean"
  | "enum"
  | "email"
  | "phone";

export interface ColumnOption {
  /** What the API takes (`"in_stock"`). */
  value: string;
  /** What the sheet shows and the dropdown lists (`"In stock"`). Import accepts either, ignoring case. */
  label: string;
}

export interface ImportColumn {
  /** Stable id: the key in `ParsedRow`, in export rows and in `sampleRows`. */
  key: string;
  /** The header text in the sheet. Matching ignores case, spacing and a trailing `*`. */
  header: string;
  kind: ColumnKind;
  required?: boolean | undefined;
  /** One sentence for the Instructions sheet: what goes in this column. */
  help: string;
  /** `kind: "enum"`: the accepted values. */
  options?: readonly ColumnOption[] | undefined;
  maxLength?: number | undefined;
  pattern?: RegExp | undefined;
  /** Shown when `pattern` fails and on the Instructions sheet ("Letters, digits, . _ -"). */
  patternHint?: string | undefined;
  /** integer, decimal: inclusive bounds. money: bounds in *major* units. */
  min?: number | undefined;
  max?: number | undefined;
  /** money: the `key` of the column holding the ISO currency (default `"currency"`). When that
   *  column is blank the value of `defaultCurrency` applies. */
  currencyKey?: string | undefined;
  /** money and currency columns: the currency used when the row names none. Default `"INR"`. */
  defaultCurrency?: string | undefined;
  /**
   * The name of a set returned by `loadLookups`. The cell must equal one of its labels (ignoring
   * case and surrounding spaces); the sheet gets a dropdown over the Lists sheet, and
   * `ctx.lookups[name].resolve(label)` gives the id in `toPayload`. Leave `kind` as `"text"`.
   */
  lookup?: string | undefined;
  /** Exported but never imported and absent from the sample sheet: ids, timestamps, masked PII,
   *  rollups. An import ignores a read-only header without complaint. */
  readOnly?: boolean | undefined;
}

/** A named set of reference data, loaded once per dialog open from the full list (all pages). */
export interface Lookup {
  /** Every valid label, in display order: the Lists sheet and the dropdown. */
  labels: readonly string[];
  /** Case-insensitive, trimmed label to the value the API wants (an id or a code). Undefined when
   *  the label is unknown or `isAmbiguous`. */
  resolve(label: string): string | undefined;
  /** More than one entry carries this label, so `resolve` refuses to guess. */
  isAmbiguous(label: string): boolean;
}

export type LookupSet = Readonly<Record<string, Lookup>>;

export interface ImportContext {
  lookups: LookupSet;
  signal: AbortSignal;
}

/** A page of the screen's list, as every list endpoint returns it. */
export interface ExportPage<T> {
  items: readonly T[];
  total: number;
  pages: number;
}

export interface ExportSource<T, Filters = void> {
  /** One page of the current list with the screen's filters. Pages are 1-based, `size` at most 200. */
  fetchPage(args: {
    page: number;
    size: number;
    filters: Filters;
    signal: AbortSignal;
  }): Promise<ExportPage<T>>;
  /** One sheet row, keyed by `ImportColumn.key`, for importable and read-only columns alike.
   *  Money is a major-unit decimal string with its currency in the currency column; a masked
   *  value is written as the API returned it. Never reveal or unmask here. */
  toRow(item: T, lookups: LookupSet): Record<string, SheetValue>;
  /** Rows written at most. Default 10 000. */
  maxRows?: number | undefined;
}

/** Thrown from `toPayload` (or `validateRow`) to fail one row with a sentence for the user. */
export class RowProblem extends Error {
  constructor(message: string) {
    super(message);
    this.name = "RowProblem";
  }
}

/**
 * One importable list. Keep it in the app (`src/data/importExport/<entity>.ts`), as a module-level
 * constant so its identity is stable across renders.
 */
export interface ImportEntity<Payload, Item = unknown, Filters = void> {
  /** `"crm.companies"`. Used for the sheet name and test ids. */
  id: string;
  /** Plural and singular nouns for the dialog's copy: `"companies"` and `"company"`. */
  noun: string;
  nounSingular: string;
  /** File name stem: `companies-sample.xlsx`, `companies-2026-09-26.xlsx`, `companies-import-result.xlsx`. */
  fileStem: string;
  columns: readonly ImportColumn[];
  /** Two or three realistic rows for the sample sheet, keyed by column key. They must pass this
   *  entity's own validation. The importer recognises an unchanged sample row and skips it. */
  sampleRows: readonly Record<string, SheetValue>[];
  /** Rows accepted per file. Default 1000. */
  maxRows?: number | undefined;
  /** Extra rules for the Instructions sheet ("Deal names must be unique across the organisation."). */
  notes?: readonly string[] | undefined;
  /** Reference data (owners, warehouses, categories) from the full lists. Called once per open. */
  loadLookups?: ((signal: AbortSignal) => Promise<LookupSet>) | undefined;
  /** Cross-column rules the per-column checks cannot express. Return a sentence, or null when fine. */
  validateRow?: ((row: ParsedRow, ctx: ImportContext) => string | null) | undefined;
  /** The body the existing create endpoint takes. Resolve lookups here; throw `RowProblem` when a
   *  name does not resolve or a rule is broken. */
  toPayload(row: ParsedRow, ctx: ImportContext): Payload;
  /** Calls the ordinary create function of the app's api client (which already honours fixture
   *  mode). Resolves with the new id when known. */
  create(payload: Payload, ctx: ImportContext): Promise<{ id?: string } | undefined | void>;
  /** Absent means the list cannot be exported from this dialog. */
  export?: ExportSource<Item, Filters> | undefined;
}

// ------------------------------------------------------------------------ session and view state

export type RowStatus = "created" | "skipped" | "failed" | "invalid";

export interface RowOutcome {
  /** The row's number in the sheet (header is row 1, so data starts at 2). */
  row: number;
  status: RowStatus;
  /** The reason for anything but `created`: the server's own sentence, or the local check's. */
  message: string | null;
  /** The new record's id, when the create returned one. */
  id?: string | undefined;
  /** The first required cell, so a report row is recognisable without the sheet open. */
  label: string;
}

export type ImportPhase = "menu" | "reading" | "preview" | "importing" | "done" | "error";

export interface PreviewRow {
  row: number;
  /** Display strings by column key (what the user typed, trimmed). */
  values: Readonly<Record<string, string>>;
  /** The reason this row will not be sent, or null. */
  error: string | null;
  /** An unchanged sample row: skipped without a message. */
  sample: boolean;
}

export interface ImportPreview {
  fileName: string;
  /** Data rows found (blank rows excluded). */
  total: number;
  valid: number;
  invalid: number;
  sample: number;
  columns: readonly { key: string; header: string }[];
  /** At most the first 200 rows, invalid ones first. */
  rows: readonly PreviewRow[];
  /** Headers in the file that match no column (ignored). */
  unknownHeaders: readonly string[];
  /** Required columns absent from the header row: the import cannot start until the file has them. */
  missingRequired: readonly string[];
}

export interface ImportProgress {
  /** Rows sent so far (any outcome). */
  done: number;
  /** Rows that will be sent. */
  total: number;
  created: number;
  skipped: number;
  failed: number;
  /** Set while the runner waits out the rate limit. */
  waiting: { reason: "rate-limit"; seconds: number } | null;
}

export interface ImportSummary {
  created: number;
  skipped: number;
  failed: number;
  invalid: number;
  /** True when the user cancelled: rows after the last one sent are not in `outcomes`. */
  cancelled: boolean;
  outcomes: readonly RowOutcome[];
}

export interface ExportProgress {
  fetched: number;
  total: number;
}

export interface DataExchangeState {
  phase: ImportPhase;
  /** A sentence for `phase: "error"` (wrong file type, too large, unreadable, too many rows). */
  error: string | null;
  preview: ImportPreview | null;
  progress: ImportProgress | null;
  summary: ImportSummary | null;
  downloadingTemplate: boolean;
  exporting: ExportProgress | null;
  /** A sentence when the last template download or export failed; cleared by the next action. */
  notice: string | null;
}

export interface DataExchangeActions {
  /** Reads and validates a chosen file; moves to `reading`, then `preview` or `error`. */
  openFile(file: File): Promise<void>;
  /** Sends the valid rows; moves to `importing`, then `done`. */
  startImport(): Promise<void>;
  /** Stops after the row in flight; the summary marks `cancelled`. */
  cancelImport(): void;
  /** Back to `menu`, discarding any preview or summary. */
  reset(): void;
  downloadTemplate(): Promise<void>;
  exportAll(): Promise<void>;
  cancelExport(): void;
  /** The per-row result of the last import as a workbook. */
  downloadReport(): Promise<void>;
}

/** What the dialog needs to know about the entity, without the functions. */
export interface DataExchangeMeta {
  noun: string;
  nounSingular: string;
  maxRows: number;
  exportMaxRows: number;
  /** Headers of required importable columns, for the "what your file needs" line. */
  requiredHeaders: readonly string[];
  /** False when the entity has no `export`. */
  canExport: boolean;
}

export interface UseImportExportOptions<Filters> {
  /** The screen's current list filters, passed to `export.fetchPage`. */
  filters?: Filters | undefined;
}

export interface UseImportExportResult {
  state: DataExchangeState;
  actions: DataExchangeActions;
  meta: DataExchangeMeta;
}

/** Type helper: gives an entity's `Payload`, `Item` and `Filters` inference at the definition site. */
export function defineImportEntity<Payload, Item = unknown, Filters = void>(
  entity: ImportEntity<Payload, Item, Filters>,
): ImportEntity<Payload, Item, Filters> {
  return entity;
}
