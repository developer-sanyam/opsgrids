/**
 * Data for the Import / Export button's stories and tests: one plausible session per phase, all
 * about companies (and, for the update-mode stories, inventory items), shaped exactly like the state
 * the data-exchange hook reports. No network and no spreadsheet, so the same pixels and the same
 * assertions on every run.
 */

import type {
  ImportExportMeta,
  ImportExportPreview,
  ImportExportPreviewAction,
  ImportExportPreviewRow,
  ImportExportProgress,
  ImportExportRowOutcome,
  ImportExportSummary,
  ImportExportViewState,
} from "./lib/importExportView";

export const XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";

export const META: ImportExportMeta = {
  noun: "companies",
  nounSingular: "company",
  maxRows: 1000,
  exportMaxRows: 10_000,
  requiredHeaders: ["Name"],
  canExport: true,
  canUpdate: false,
  recordIdHeader: "Company ID",
};

/** A list that can be updated from a sheet, so the dialog offers "Add new and update existing". */
export const UPDATE_META: ImportExportMeta = {
  noun: "items",
  nounSingular: "item",
  maxRows: 1000,
  exportMaxRows: 10_000,
  requiredHeaders: ["SKU", "Name"],
  canExport: true,
  canUpdate: true,
  recordIdHeader: "Item ID",
};

/** The dialog as it opens: nothing chosen, nothing running. */
export const MENU_STATE: ImportExportViewState = {
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

const COLUMNS = [
  { key: "name", header: "Name" },
  { key: "industry", header: "Industry" },
  { key: "city", header: "City" },
  { key: "website", header: "Website" },
  { key: "employees", header: "Employees" },
] as const;

function previewRow(
  row: number,
  values: [name: string, industry: string, city: string, website: string, employees: string],
  extra: { error?: string; sample?: boolean } = {},
): ImportExportPreviewRow {
  const [name, industry, city, website, employees] = values;
  const action: ImportExportPreviewAction =
    extra.error !== undefined ? "invalid" : extra.sample === true ? "sample" : "create";
  return {
    row,
    values: { name, industry, city, website, employees },
    error: extra.error ?? null,
    sample: extra.sample ?? false,
    action,
    changes: [],
    record: null,
    note: null,
  };
}

/** Three rows that cannot be sent (listed first, as the engine lists them), two sample rows, five good ones. */
export const PREVIEW_WITH_ERRORS: ImportExportPreview = {
  fileName: "companies-september.xlsx",
  mode: "add",
  total: 10,
  valid: 5,
  invalid: 3,
  sample: 2,
  create: 5,
  update: 0,
  unchanged: 0,
  columns: COLUMNS,
  rows: [
    previewRow(6, ["", "Freight", "Mumbai", "https://bluefin.example", "48"], { error: "Name is required." }),
    previewRow(9, ["Northwind Traders", "Retail", "Chennai", "northwind", "120"], {
      error: "Website must start with http:// or https://.",
    }),
    previewRow(11, ["Tandon Textiles", "Manufacturing", "Surat", "https://tandon.example", "twelve"], {
      error: "Employees must be a whole number.",
    }),
    previewRow(2, ["Example Trading Co", "Wholesale", "Pune", "https://example.com", "25"], { sample: true }),
    previewRow(3, ["Example Foods Ltd", "Food", "Nashik", "https://example.org", "60"], { sample: true }),
    previewRow(4, ["Acme Industries", "Manufacturing", "Pune", "https://acme.example", "240"]),
    previewRow(5, ["Bluefin Logistics", "Freight", "Mumbai", "https://bluefin.example", "48"]),
    previewRow(7, ["Kaveri Agro", "Agriculture", "Mysuru", "", "31"]),
    previewRow(8, ["Meridian Health", "Healthcare", "Hyderabad", "https://meridian.example", "410"]),
    previewRow(10, ["Orchid Interiors", "Design", "Kochi", "https://orchid.example", ""]),
  ],
  unknownHeaders: ["Nickname"],
  missingRequired: [],
  matchWarning: null,
  baseline: null,
};

/** One new row, one that is already on file (skipped, not wrong) and one that needs fixing. */
export const PREVIEW_WITH_EXISTING: ImportExportPreview = {
  ...PREVIEW_WITH_ERRORS,
  total: 3,
  valid: 1,
  invalid: 1,
  exists: 1,
  sample: 0,
  create: 1,
  rows: [
    previewRow(4, ["", "Freight", "Mumbai", "https://bluefin.example", "48"], { error: "Name is required." }),
    {
      ...previewRow(3, ["Acme Industries", "Manufacturing", "Pune", "https://acme.example", "240"]),
      action: "exists",
      note: "A company with that name is already on file.",
    },
    previewRow(2, ["Kaveri Agro", "Agriculture", "Mysuru", "", "31"]),
  ],
};

/** The file has no Name column, so no row can be sent, whatever else is in it. */
export const PREVIEW_MISSING_COLUMNS: ImportExportPreview = {
  fileName: "customer-list.xlsx",
  mode: "add",
  total: 4,
  valid: 0,
  invalid: 4,
  sample: 0,
  create: 0,
  update: 0,
  unchanged: 0,
  columns: COLUMNS.filter((column) => column.key !== "name"),
  rows: [
    previewRow(2, ["", "Manufacturing", "Pune", "https://acme.example", "240"], { error: "Name is required." }),
    previewRow(3, ["", "Freight", "Mumbai", "https://bluefin.example", "48"], { error: "Name is required." }),
    previewRow(4, ["", "Agriculture", "Mysuru", "", "31"], { error: "Name is required." }),
    previewRow(5, ["", "Healthcare", "Hyderabad", "https://meridian.example", "410"], { error: "Name is required." }),
  ],
  unknownHeaders: ["Nickname", "Account manager"],
  missingRequired: ["Name"],
  matchWarning: null,
  baseline: null,
};

const ITEM_COLUMNS = [
  { key: "sku", header: "SKU" },
  { key: "name", header: "Name" },
  { key: "price", header: "Price" },
  { key: "quantity", header: "Quantity" },
] as const;

function itemRow(
  row: number,
  values: [sku: string, name: string, price: string, quantity: string],
  action: ImportExportPreviewAction,
  extra: { error?: string; changes?: readonly string[]; record?: string; note?: string } = {},
): ImportExportPreviewRow {
  const [sku, name, price, quantity] = values;
  return {
    row,
    values: { sku, name, price, quantity },
    error: extra.error ?? null,
    sample: action === "sample",
    action,
    changes: extra.changes ?? [],
    record: extra.record ?? null,
    note: extra.note ?? null,
  };
}

/** Update mode on a real file: three rows change a record, two add one, two already match, one is refused. */
export const PREVIEW_UPDATE_MIXED: ImportExportPreview = {
  fileName: "items-september.xlsx",
  mode: "addAndUpdate",
  total: 8,
  valid: 5,
  invalid: 1,
  sample: 0,
  create: 2,
  update: 3,
  unchanged: 2,
  columns: ITEM_COLUMNS,
  rows: [
    itemRow(6, ["BRK-200", "Bracket, galvanised", "980", ""], "invalid", {
      error: "More than one record has that SKU, so the row cannot say which to update.",
    }),
    itemRow(2, ["BRK-100", "Steel bracket", "1499.50", ""], "update", {
      changes: ["Price"],
      record: "Steel bracket (BRK-100)",
    }),
    itemRow(3, ["BRK-110", "Steel bracket, white", "", "40"], "update", {
      changes: ["Quantity"],
      record: "Steel bracket, white (BRK-110)",
    }),
    itemRow(4, ["BRK-120", "Bracket, zinc", "12", ""], "update", {
      changes: ["Name", "Price"],
      record: "Bracket, zinc plated (BRK-120)",
    }),
    itemRow(5, ["NUT-500", "Hex nut M8", "2.50", "5000"], "create"),
    itemRow(7, ["NUT-510", "Hex nut M10", "3", "2500"], "create"),
    itemRow(8, ["BRK-130", "Bracket, black", "", ""], "unchanged", { record: "Bracket, black (BRK-130)" }),
    itemRow(9, ["BRK-140", "Bracket, brass", "", ""], "unchanged", { record: "Bracket, brass (BRK-140)" }),
  ],
  unknownHeaders: [],
  missingRequired: [],
  matchWarning: null,
  baseline: "used",
};

/**
 * A file with no export baseline (a sample sheet, or an export an editor saved without it): every
 * filled cell is compared with the record as it is now, so the preview warns that an old cell puts
 * its old value back. One row is found by a name written in other capitals and changes nothing.
 */
export const PREVIEW_UPDATE_NO_BASELINE: ImportExportPreview = {
  fileName: "prices-copied-from-june.xlsx",
  mode: "addAndUpdate",
  total: 3,
  valid: 2,
  invalid: 0,
  sample: 0,
  create: 0,
  update: 2,
  unchanged: 1,
  columns: ITEM_COLUMNS,
  rows: [
    itemRow(2, ["BRK-100", "Steel bracket", "1499.50", "120"], "update", {
      changes: ["Price", "Quantity"],
      record: "Steel bracket (BRK-100)",
    }),
    itemRow(3, ["BRK-110", "Steel bracket, white", "18", "40"], "update", {
      changes: ["Price"],
      record: "Steel bracket, white (BRK-110)",
    }),
    itemRow(4, ["brk-120", "Bracket, zinc plated", "12", "300"], "unchanged", {
      record: "Bracket, zinc plated (BRK-120)",
      note: "The SKU differs from the record's only in capitals, and a row found by it cannot change that, so nothing was sent. Use the Item ID to change only the capitals of a name.",
    }),
  ],
  unknownHeaders: [],
  missingRequired: [],
  matchWarning: null,
  baseline: "none",
};

/** The file carries a baseline that is damaged or another list's: it is ignored, and the preview says so. */
export const PREVIEW_UPDATE_UNUSABLE_BASELINE: ImportExportPreview = {
  ...PREVIEW_UPDATE_NO_BASELINE,
  fileName: "items-export-edited.xlsx",
  baseline: "unusable",
};

/** A list matched by its id column alone, with rows that have no id: they are added as new records, and the count says so. */
export const PREVIEW_UPDATE_ROWS_WITHOUT_ID: ImportExportPreview = {
  fileName: "items-with-new-rows.xlsx",
  mode: "addAndUpdate",
  total: 3,
  valid: 3,
  invalid: 0,
  sample: 0,
  create: 2,
  update: 1,
  unchanged: 0,
  columns: ITEM_COLUMNS,
  rows: [
    itemRow(2, ["", "Steel bracket, galvanised", "", ""], "update", {
      changes: ["Name"],
      record: "Steel bracket (BRK-100)",
    }),
    itemRow(3, ["", "Hex nut M8", "2.50", "5000"], "create"),
    itemRow(4, ["", "Hex nut M10", "3", "2500"], "create"),
  ],
  unknownHeaders: [],
  missingRequired: [],
  matchWarning: "2 rows have no Item ID, so they will be created as new records.",
  baseline: "used",
};

/** Every row already matches its record: there is nothing to send, and the button says so. */
export const PREVIEW_UPDATE_ALL_UNCHANGED: ImportExportPreview = {
  fileName: "items-checked.xlsx",
  mode: "addAndUpdate",
  total: 4,
  valid: 0,
  invalid: 0,
  sample: 0,
  create: 0,
  update: 0,
  unchanged: 4,
  columns: ITEM_COLUMNS,
  rows: [
    itemRow(2, ["BRK-100", "Steel bracket", "1499.50", "120"], "unchanged", { record: "Steel bracket (BRK-100)" }),
    itemRow(3, ["BRK-110", "Steel bracket, white", "18", "40"], "unchanged", { record: "Steel bracket, white (BRK-110)" }),
    itemRow(4, ["BRK-120", "Bracket, zinc", "12", "300"], "unchanged", { record: "Bracket, zinc plated (BRK-120)" }),
    itemRow(5, ["BRK-130", "Bracket, black", "", ""], "unchanged", { record: "Bracket, black (BRK-130)" }),
  ],
  unknownHeaders: [],
  missingRequired: [],
  matchWarning: null,
  baseline: "used",
};

/** The ways an update row is refused before anything is sent, each with its reason. */
export const PREVIEW_UPDATE_REFUSALS: ImportExportPreview = {
  fileName: "items-from-export.xlsx",
  mode: "addAndUpdate",
  total: 5,
  valid: 1,
  invalid: 4,
  sample: 0,
  create: 0,
  update: 1,
  unchanged: 0,
  columns: ITEM_COLUMNS,
  rows: [
    itemRow(3, ["BRK-110", "[clear]", "", ""], "invalid", { error: "Name cannot be cleared" }),
    itemRow(4, ["BRK-999", "Steel bracket", "1499.50", ""], "invalid", {
      error:
        "That Item ID is not in this list: the record may have been deleted, the file came from another organisation, or it is outside the records you can see.",
    }),
    itemRow(6, ["BRK-101", "Steel bracket", "", ""], "invalid", {
      error: "SKU cannot be changed once the record exists (it is 'BRK-100'). Leave the cell as it is, or blank.",
      record: "Steel bracket (BRK-100)",
    }),
    itemRow(7, ["BRK-100", "Steel bracket", "1400", ""], "invalid", {
      error: "This row updates the same record as row 2. Keep only one of the two rows.",
      record: "Steel bracket (BRK-100)",
    }),
    itemRow(2, ["BRK-100", "Steel bracket", "1499.50", ""], "update", {
      changes: ["Price"],
      record: "Steel bracket (BRK-100)",
    }),
  ],
  unknownHeaders: [],
  missingRequired: [],
  matchWarning: null,
  baseline: "used",
};

/** Update mode was chosen but the file has nothing that names a record: every row would be added as new. */
export const PREVIEW_UPDATE_CANNOT_MATCH: ImportExportPreview = {
  fileName: "prices.xlsx",
  mode: "addAndUpdate",
  total: 3,
  valid: 0,
  invalid: 3,
  sample: 0,
  create: 0,
  update: 0,
  unchanged: 0,
  columns: ITEM_COLUMNS.filter((column) => column.key === "name" || column.key === "price"),
  rows: [
    itemRow(2, ["", "Steel bracket", "1499.50", ""], "invalid", { error: "SKU is required to add a new record" }),
    itemRow(3, ["", "Steel bracket, white", "18", ""], "invalid", { error: "SKU is required to add a new record" }),
    itemRow(4, ["", "Bracket, zinc", "12", ""], "invalid", { error: "SKU is required to add a new record" }),
  ],
  unknownHeaders: [],
  missingRequired: [],
  matchWarning:
    "This file has no Item ID or SKU column, so it cannot update existing records; every row would be added as new.",
  baseline: null,
};

export const PROGRESS_RUNNING: ImportExportProgress = {
  done: 46,
  total: 120,
  created: 40,
  updated: 0,
  skipped: 4,
  failed: 2,
  waiting: null,
};

export const PROGRESS_WAITING: ImportExportProgress = {
  ...PROGRESS_RUNNING,
  waiting: { reason: "rate-limit", seconds: 42 },
};

/** An update run part way: new records and changed ones both count, and nothing is ever skipped. */
export const PROGRESS_UPDATING: ImportExportProgress = {
  done: 46,
  total: 120,
  created: 6,
  updated: 38,
  skipped: 0,
  failed: 2,
  waiting: null,
};

const NAMES = [
  "Acme Industries",
  "Bluefin Logistics",
  "Kaveri Agro",
  "Meridian Health",
  "Orchid Interiors",
  "Tandon Textiles",
];

function created(count: number): ImportExportRowOutcome[] {
  return NAMES.slice(0, count).map((label, index) => ({
    row: index + 2,
    status: "created",
    message: null,
    id: `company-${index + 1}`,
    label,
  }));
}

/** Every row went in: no problem rows to list, so the result says everything imported. */
export const SUMMARY_CLEAN: ImportExportSummary = {
  created: 6,
  updated: 0,
  unchanged: 0,
  skipped: 0,
  failed: 0,
  invalid: 0,
  cancelled: false,
  outcomes: created(6),
};

export const SUMMARY_WITH_PROBLEMS: ImportExportSummary = {
  created: 4,
  updated: 0,
  unchanged: 0,
  skipped: 2,
  failed: 2,
  invalid: 1,
  cancelled: false,
  outcomes: [
    ...created(4),
    {
      row: 6,
      status: "skipped",
      message: "A company with this name already exists.",
      label: "Northwind Traders",
    },
    {
      row: 7,
      status: "skipped",
      message: "A company with this name already exists.",
      label: "Sundaram Steel",
    },
    {
      row: 8,
      status: "failed",
      message: "You can't assign Priya Raman as the owner of a company.",
      label: "Lotus Packaging",
    },
    {
      row: 9,
      status: "failed",
      message: "The server could not be reached. Try this row again.",
      label: "Zenith Foods",
    },
    { row: 10, status: "invalid", message: "Owner email is not a member of this organization.", label: "Harbor Exports" },
  ],
};

/** Stopped by the user after two rows; the rows after the last one sent are not in the list. */
export const SUMMARY_CANCELLED: ImportExportSummary = {
  created: 2,
  updated: 0,
  unchanged: 0,
  skipped: 0,
  failed: 0,
  invalid: 0,
  cancelled: true,
  outcomes: created(2),
};

/** An update run that went through: three rows changed, two were added, five already matched. */
export const SUMMARY_UPDATE_CLEAN: ImportExportSummary = {
  created: 2,
  updated: 3,
  unchanged: 5,
  skipped: 0,
  failed: 0,
  invalid: 0,
  cancelled: false,
  outcomes: [
    { row: 2, status: "updated", message: null, label: "BRK-100", changes: ["Price"], record: "Steel bracket (BRK-100)" },
    {
      row: 3,
      status: "updated",
      message: null,
      label: "BRK-110",
      changes: ["Quantity"],
      record: "Steel bracket, white (BRK-110)",
    },
    {
      row: 4,
      status: "updated",
      message: null,
      label: "BRK-120",
      changes: ["Name", "Price"],
      record: "Bracket, zinc plated (BRK-120)",
    },
    { row: 5, status: "created", message: null, id: "item-501", label: "NUT-500" },
    { row: 6, status: "created", message: null, id: "item-502", label: "NUT-510" },
    ...[7, 8, 9, 10, 11].map(
      (row): ImportExportRowOutcome => ({
        row,
        status: "unchanged",
        message: "Every cell already matches the record, so nothing was sent.",
        label: `BRK-${row + 120}`,
        record: `Bracket (BRK-${row + 120})`,
      }),
    ),
  ],
};

/** Three updated, two created, five unchanged, and one the server refused with a collision. */
export const SUMMARY_UPDATE_WITH_FAILURES: ImportExportSummary = {
  ...SUMMARY_UPDATE_CLEAN,
  failed: 1,
  outcomes: [
    ...SUMMARY_UPDATE_CLEAN.outcomes,
    {
      row: 12,
      status: "failed",
      message: "An item named 'Steel bracket, white' already exists.",
      label: "BRK-150",
      changes: ["Name"],
      record: "Bracket, nickel (BRK-150)",
    },
  ],
};

/** A row whose first request went through and a later one did not: the message says which columns were saved and which were not. */
export const SUMMARY_UPDATE_PARTLY_SAVED: ImportExportSummary = {
  created: 0,
  updated: 1,
  unchanged: 0,
  skipped: 0,
  failed: 1,
  invalid: 0,
  cancelled: false,
  outcomes: [
    { row: 2, status: "updated", message: null, label: "BRK-100", changes: ["Price"], record: "Steel bracket (BRK-100)" },
    {
      row: 3,
      status: "failed",
      message: "The request could not be completed. Check your connection and try again. Saved: Name, Price. Not saved: Stage.",
      label: "BRK-110",
      changes: ["Name", "Price", "Stage"],
      record: "Steel bracket, white (BRK-110)",
    },
  ],
};

export function stateFor(overrides: Partial<ImportExportViewState>): ImportExportViewState {
  return { ...MENU_STATE, ...overrides };
}
