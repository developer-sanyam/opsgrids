/**
 * Data for the Import / Export button's stories and tests: one plausible session per phase, all
 * about companies, shaped exactly like the state the data-exchange hook reports. No network and no
 * spreadsheet, so the same pixels and the same assertions on every run.
 */

import type {
  ImportExportMeta,
  ImportExportPreview,
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
};

/** The dialog as it opens: nothing chosen, nothing running. */
export const MENU_STATE: ImportExportViewState = {
  phase: "menu",
  error: null,
  preview: null,
  progress: null,
  summary: null,
  downloadingTemplate: false,
  exporting: null,
  notice: null,
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
  return {
    row,
    values: { name, industry, city, website, employees },
    error: extra.error ?? null,
    sample: extra.sample ?? false,
  };
}

/** Three rows that cannot be sent (listed first, as the engine lists them), two sample rows, five good ones. */
export const PREVIEW_WITH_ERRORS: ImportExportPreview = {
  fileName: "companies-september.xlsx",
  total: 10,
  valid: 5,
  invalid: 3,
  sample: 2,
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
};

/** The file has no Name column, so no row can be sent, whatever else is in it. */
export const PREVIEW_MISSING_COLUMNS: ImportExportPreview = {
  fileName: "customer-list.xlsx",
  total: 4,
  valid: 0,
  invalid: 4,
  sample: 0,
  columns: COLUMNS.filter((column) => column.key !== "name"),
  rows: [
    previewRow(2, ["", "Manufacturing", "Pune", "https://acme.example", "240"], { error: "Name is required." }),
    previewRow(3, ["", "Freight", "Mumbai", "https://bluefin.example", "48"], { error: "Name is required." }),
    previewRow(4, ["", "Agriculture", "Mysuru", "", "31"], { error: "Name is required." }),
    previewRow(5, ["", "Healthcare", "Hyderabad", "https://meridian.example", "410"], { error: "Name is required." }),
  ],
  unknownHeaders: ["Nickname", "Account manager"],
  missingRequired: ["Name"],
};

export const PROGRESS_RUNNING: ImportExportProgress = {
  done: 46,
  total: 120,
  created: 40,
  skipped: 4,
  failed: 2,
  waiting: null,
};

export const PROGRESS_WAITING: ImportExportProgress = {
  ...PROGRESS_RUNNING,
  waiting: { reason: "rate-limit", seconds: 42 },
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
  skipped: 0,
  failed: 0,
  invalid: 0,
  cancelled: false,
  outcomes: created(6),
};

export const SUMMARY_WITH_PROBLEMS: ImportExportSummary = {
  created: 4,
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
  skipped: 0,
  failed: 0,
  invalid: 0,
  cancelled: true,
  outcomes: created(2),
};

export function stateFor(overrides: Partial<ImportExportViewState>): ImportExportViewState {
  return { ...MENU_STATE, ...overrides };
}
