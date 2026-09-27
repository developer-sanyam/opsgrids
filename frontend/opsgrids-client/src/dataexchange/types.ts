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
 *  minor units, an enum is the option's `value`, an email keeps its local part exactly as typed and
 *  has its domain lower-cased (the server dedupes on that string), a phone keeps `+` and digits
 *  only. */
export type ParsedValue = string | number | boolean | null;

/** One row after coercion, keyed by `ImportColumn.key`. Blank optional cells are `null`, with one
 *  exception: a blank currency cell (the column a money column's `currencyKey` names) parses as
 *  that money column's `defaultCurrency`, not `null`, so an entity can tell that the row's money is
 *  priced. An entity sends the currency only alongside an amount. */
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
  /** money and currency columns: the currency used when the row names none. Default `"INR"`. A
   *  blank currency cell therefore parses as this value (not `null`), so an entity can tell that the
   *  row's money is priced; it should send the currency only alongside an amount. */
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
  /** The opposite of `readOnly`: read on import and shown in the sample sheet, but not written by
   *  an export because the list never returns it (opening stock, a value only create accepts). */
  importOnly?: boolean | undefined;
  /** Free text that may hold tabs and line breaks (a note, a summary). Every other text column
   *  refuses them: a name or code with a hidden line break becomes a different record. */
  multiline?: boolean | undefined;
  /** `lookup` columns: a sentence added to the "is not in the list" error saying how to fix it
   *  ("Import the manager first, then run this file again: rows already imported are skipped."). */
  lookupHint?: string | undefined;
  /** `lookup` columns: the hint for a row that updates an existing record, used instead of
   *  `lookupHint` where the add-mode advice is wrong ("Leave blank to assign it to yourself": when
   *  updating, blank leaves the field alone). Absent: `lookupHint` applies to both. */
  lookupHintUpdate?: string | undefined;
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
  /** The reverse of `resolve`: the label whose value is `value` (an id or code), for writing a
   *  reference back into an export as the text an import would accept. Undefined when unknown. */
  labelOf(value: string): string | undefined;
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

// ------------------------------------------------------------------ update mode (ADR-0022 addendum)

/** `add`: rows create records (today's behaviour). `addAndUpdate`: a row that names an existing
 *  record (by its exported Record ID, or by the list's unique key) updates it instead. */
export type ImportMode = "add" | "addAndUpdate";

/** The text a cell holds to empty a field. A blank cell means "leave it alone". */
export const CLEAR_TOKEN = "[clear]";

/** One column an update may change (or, when `immutable`, must not). */
export interface UpdateField<Item> {
  /** The `ImportColumn.key` of the sheet column. */
  column: string;
  /** The key sent in the update body. Default: `column`. */
  field?: string | undefined;
  /** The stored value, in the parsed cell's own terms (money as minor units, an ISO date, an
   *  enum's `value`, a lookup's *value*), with an empty stored value as `null`. `undefined` means
   *  the list never returns it (opening stock): an immutable field then refuses any filled cell
   *  without claiming what is stored, and an updatable one is always sent. */
  current(item: Item, ctx: ImportContext): ParsedValue | undefined;
  /** Turns the parsed cell into the stored terms when they differ (a lookup column's parsed value
   *  is its label; return the id or code the list stores). May throw `RowProblem`. */
  resolve?: ((value: ParsedValue, ctx: ImportContext) => ParsedValue) | undefined;
  /** The value sent in the body for a changed field. Default: the resolved value. */
  toWire?: ((value: ParsedValue) => unknown) | undefined;
  /** What to send to EMPTY the field: `""` or `null`, whichever that endpoint accepts. Absent means
   *  the field cannot be cleared and a `[clear]` cell refuses the row. Never guess `null`:
   *  several endpoints answer it with a 409 or a 500. */
  clear?: "" | null | undefined;
  /** Compares a cell (already resolved) with the stored value. Default: strict equality. */
  same?: ((cell: ParsedValue, stored: ParsedValue) => boolean) | undefined;
  /** Identity or otherwise unchangeable (SKU, code, employee number, currency on some lists): never
   *  sent. A cell that differs from the stored value refuses the row instead of being ignored. */
  immutable?: boolean | undefined;
}

/** An update that is not a plain field change (a deal's stage move, a contact's phone). */
export interface ExtraStep {
  /** Headers of the columns this step changes, for the preview. */
  changed: readonly string[];
  run(): Promise<void>;
}

/** How an existing list is updated from a sheet. Optional on an entity: absent means the list can
 *  only be added to. */
export interface UpdateSpec<Item> {
  /** Every record of the list (all pages, keyed by id): what rows are matched and diffed against. */
  loadExisting(ctx: ImportContext): Promise<readonly Item[]>;
  /** The record's own id: what a Record ID cell carries. */
  idOf(item: Item): string;
  /** The `ImportColumn.key` of the sheet column holding the list's unique key (SKU, code, employee
   *  number, asset tag), for rows that have no Record ID. Absent: rows match by Record ID only. */
  keyColumn?: string | undefined;
  /** The unique key of a stored record, or null when it has none. Matching ignores case and spacing. */
  keyOf?(item: Item): string | null;
  /** Which stored record a row updates, in words a person recognises (a company's name, a deal's
   *  name, a lead's name, an item's SKU and name). Shown beside every update row in the preview and
   *  in the result report, so a shifted Record ID column is seen before anything is written. Never
   *  a sealed value (a lead's email or phone, a contact's phone, employee personal data). */
  recordLabel?(item: Item): string;
  /** The stored record's ISO currency, for lists whose amounts carry one. On an update, a money cell
   *  is converted with THIS currency when the row names none, and a blank Currency cell stays blank
   *  (unchanged) instead of defaulting to INR, which would silently re-price a yen or dinar record. */
  currencyOf?(item: Item): string | null;
  fields: readonly UpdateField<Item>[];
  /**
   * Importable columns an update deliberately does not handle (lead email and phone, which have no
   * update endpoint, a column only a create sets). A filled cell in one is REFUSED ("<Header> cannot
   * be changed by an update: leave the cell blank"), never silently ignored, so nobody believes a
   * field changed when it did not. `checkImportEntity` fails an update spec in which an importable
   * column is neither a `fields[].column` (nor the key or Record ID column) nor listed here.
   */
  notUpdatable?: readonly string[];
  /** Columns handled by an `extras` step rather than a field (a contact's phone): they count as
   *  covered for the `notUpdatable` completeness check. */
  extraColumns?: readonly string[];
  /** Refuses a change the list's own rules forbid, given what would change (a converted lead's
   *  status, a closed deal's stage): throw `RowProblem`. Runs after the diff. */
  check?(row: ParsedRow, existing: Item, changed: readonly string[], ctx: ImportContext): void;
  /** Sends the changed fields (only those) to the list's ordinary update endpoint. */
  send(existing: Item, body: Readonly<Record<string, unknown>>, ctx: ImportContext): Promise<void>;
  /** Steps that are not plain field changes, run after `send`. */
  extras?(
    row: ParsedRow,
    cleared: ReadonlySet<string>,
    existing: Item,
    ctx: ImportContext,
  ): readonly ExtraStep[];
}

/**
 * How the runner sends one request of a plan: it waits for its turn (throttling), waits out a 429
 * and tries that request again, and gives up when the run is cancelled. A plan hands each of its
 * requests (the field update, then every extra step) to it, so a 429 retries only the request that
 * received it: a 429 request never executed, but the ones before it did and must not be repeated.
 */
export type StepRunner = (step: () => Promise<void>) => Promise<void>;

/** What one matched row will do: the headers it changes, and the calls that do it. */
export interface UpdatePlan {
  changed: readonly string[];
  /** Sends the changed fields, then each extra step. Every request goes through `around` when given,
   *  which is how the runner paces it; each `send` and each extra step must be ONE request. */
  run(around?: StepRunner): Promise<void>;
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
  /**
   * The `key` of a `readOnly` column that holds the record's own id (an export writes it). A row
   * with a value there came out of the system: it is refused with an explanation instead of being
   * added a second time. Essential where the list has no natural key to dedupe on (leads, whose
   * email is masked on export), harmless elsewhere.
   */
  recordIdKey?: string | undefined;
  /** Reference data (owners, warehouses, categories) from the full lists. Called once per open. */
  loadLookups?: ((signal: AbortSignal) => Promise<LookupSet>) | undefined;
  /** Cross-column rules the per-column checks cannot express. Return a sentence, or null when fine.
   *  Judges rows that create a record: a row that updates one is left to `update.check`, since a
   *  partial row (blank cells leave fields alone) would fail rules written for a complete one. */
  validateRow?: ((row: ParsedRow, ctx: ImportContext) => string | null) | undefined;
  /** The body the existing create endpoint takes. Resolve lookups here; throw `RowProblem` when a
   *  name does not resolve or a rule is broken. */
  toPayload(row: ParsedRow, ctx: ImportContext): Payload;
  /** Calls the ordinary create function of the app's api client (which already honours fixture
   *  mode). Resolves with the new id when known. */
  create(payload: Payload, ctx: ImportContext): Promise<{ id?: string } | undefined | void>;
  /** Absent means the list cannot be exported from this dialog. */
  export?: ExportSource<Item, Filters> | undefined;
  /** Absent means the list can only be added to: the dialog offers no "update existing" mode. */
  update?: UpdateSpec<Item> | undefined;
}

// ------------------------------------------------------------------------ session and view state

export type RowStatus = "created" | "updated" | "unchanged" | "skipped" | "failed" | "invalid";

export interface RowOutcome {
  /** The row's number in the sheet (header is row 1, so data starts at 2). */
  row: number;
  status: RowStatus;
  /** The reason for anything but `created` and `updated`: the server's own sentence, or the local check's. */
  message: string | null;
  /** The new record's id, when the create returned one. */
  id?: string | undefined;
  /** An update row: the headers of the columns it changes (or tried to change, when it failed). */
  changes?: readonly string[] | undefined;
  /** The first required cell, so a report row is recognisable without the sheet open. */
  label: string;
}

export type ImportPhase = "menu" | "reading" | "preview" | "importing" | "done" | "error";

/** What the import will do with one row. `unchanged` is an update row that already matches its
 *  record: no request. `sample` is an unchanged example row: skipped without a message. */
export type PreviewAction = "create" | "update" | "unchanged" | "invalid" | "sample";

export interface PreviewRow {
  row: number;
  /** Display strings by column key (what the user typed, trimmed). */
  values: Readonly<Record<string, string>>;
  /** The reason this row will not be sent, or null. */
  error: string | null;
  /** An unchanged sample row: skipped without a message. */
  sample: boolean;
  action: PreviewAction;
  /** `update` rows: the headers of the columns that change. Empty otherwise. */
  changes: readonly string[];
}

export interface ImportPreview {
  fileName: string;
  /** The mode this preview was computed for. */
  mode: ImportMode;
  /** Data rows found (blank rows excluded): `create + update + unchanged + invalid + sample`. */
  total: number;
  /** Rows that will be sent: `create + update`. */
  valid: number;
  invalid: number;
  sample: number;
  /** Rows that will add a record. In `add` mode this is `valid`. */
  create: number;
  /** Rows that will change a record (update mode only). */
  update: number;
  /** Rows that match a record and change nothing: no request is made for them (update mode only). */
  unchanged: number;
  columns: readonly { key: string; header: string }[];
  /** At most the first 200 rows, invalid ones first. */
  rows: readonly PreviewRow[];
  /** Headers in the file that match no column (ignored). */
  unknownHeaders: readonly string[];
  /** Required columns absent from the header row: the import cannot start until the file has them.
   *  Always empty in `addAndUpdate` mode: a row that updates a record needs none of them (a blank cell
   *  leaves the field alone), and a row that adds one says what it lacks. */
  missingRequired: readonly string[];
  /** A sentence when update mode was chosen but the file has no column that can name a record (no
   *  Record ID and no key column), so every row would be added as new. Null otherwise, and in `add` mode. */
  matchWarning: string | null;
}

export interface ImportProgress {
  /** Rows sent so far (any outcome). */
  done: number;
  /** Rows that will be sent. */
  total: number;
  created: number;
  updated: number;
  skipped: number;
  failed: number;
  /** Set while the runner waits out the rate limit. */
  waiting: { reason: "rate-limit"; seconds: number } | null;
}

export interface ImportSummary {
  created: number;
  updated: number;
  /** Update rows that matched a record and changed nothing: nothing was sent for them. */
  unchanged: number;
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
  /** What an import does with a row that names an existing record. `add` until the user chooses. */
  mode: ImportMode;
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
  /** Chooses what an import does with rows that name an existing record. On a preview it checks the
   *  file again in the new mode, from the cells already read (the file is not read twice). Ignored
   *  while rows are being sent. */
  setMode(mode: ImportMode): Promise<void>;
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
  /** True when the entity has an `update` spec: the dialog offers "Add new and update existing". */
  canUpdate: boolean;
}

export interface UseImportExportOptions<Filters> {
  /** The screen's current list filters, passed to `export.fetchPage`. */
  filters?: Filters | undefined;
  /** Called once when an import run ends (finished or cancelled) having created or updated at least
   *  one record: the screen reloads its list here so the new and changed rows appear without a refresh. */
  onImported?: ((summary: ImportSummary) => void) | undefined;
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
