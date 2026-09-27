/**
 * Shared by the dataexchange tests: a realistic entity that exercises every column kind, and
 * helpers to build a workbook in memory and hand it over as a `File`. Not exported from the
 * package; nothing but the tests imports it.
 */

import type * as ExcelJS from "exceljs";

import { loadExcel, loadableBytes, readBytes } from "./excel";
import { minorToMajor } from "./money";
import { makeLookup } from "./lookups";
import { defineImportEntity } from "./types";
import type { ImportEntity, ImportColumn, LookupSet, ParsedRow, SheetValue, UpdateSpec } from "./types";
import { RowProblem } from "./types";

export interface ProductPayload {
  sku: string;
  name: string;
  status: string | null;
  categoryId: string | null;
  priceMinor: number | null;
  currency: string | null;
  quantity: number | null;
  weight: number | null;
  launched: string | null;
  active: boolean | null;
  email: string | null;
  phone: string | null;
}

export interface ProductItem {
  id: string;
  sku: string;
  name: string;
  status: string;
  category: string;
  priceMinor: number;
  currency: string;
  quantity: number;
  active: boolean;
  email: string;
  createdAt: string;
}

export const productColumns: readonly ImportColumn[] = [
  { key: "id", header: "Id", kind: "text", help: "Assigned by the system.", readOnly: true },
  {
    key: "sku",
    header: "SKU",
    kind: "text",
    required: true,
    help: "The stock keeping unit; unique in the organisation.",
    maxLength: 20,
    pattern: /^[A-Za-z0-9._-]+$/,
    patternHint: "Letters, digits, . _ -",
  },
  { key: "name", header: "Name", kind: "text", required: true, help: "What the product is called.", maxLength: 60 },
  {
    key: "status",
    header: "Status",
    kind: "enum",
    help: "Where the product is in its life.",
    options: [
      { value: "in_stock", label: "In stock" },
      { value: "discontinued", label: "Discontinued" },
    ],
  },
  { key: "category", header: "Category", kind: "text", help: "The product's category.", lookup: "categories" },
  { key: "price", header: "Price", kind: "money", help: "Selling price per unit.", min: 0 },
  {
    key: "currency",
    header: "Currency",
    kind: "enum",
    help: "The currency of the price.",
    defaultCurrency: "INR",
    options: [
      { value: "INR", label: "INR" },
      { value: "USD", label: "USD" },
      { value: "JPY", label: "JPY" },
      { value: "KWD", label: "KWD" },
    ],
  },
  { key: "quantity", header: "Quantity", kind: "integer", help: "Units on hand.", min: 0, max: 1_000_000 },
  { key: "weight", header: "Weight (kg)", kind: "decimal", help: "Weight in kilograms.", min: 0 },
  { key: "launched", header: "Launch date", kind: "date", help: "When it went on sale." },
  { key: "active", header: "Active", kind: "boolean", help: "Whether it can be sold." },
  { key: "email", header: "Supplier email", kind: "email", help: "Who to reorder from." },
  { key: "phone", header: "Supplier phone", kind: "phone", help: "The supplier's phone, with country code." },
  { key: "created", header: "Created", kind: "date", help: "When the record was created.", readOnly: true },
];

export const productSamples: readonly Record<string, SheetValue>[] = [
  {
    sku: "0012-BLK",
    name: "Steel bracket",
    status: "In stock",
    category: "Hardware",
    price: "1499.50",
    currency: "INR",
    quantity: 120,
    weight: 0.75,
    launched: "2026-03-04",
    active: true,
    email: "orders@example.com",
    phone: "+919876543210",
  },
  {
    sku: "0013-WHT",
    name: "Steel bracket, white",
    status: "discontinued",
    category: "Hardware",
    price: "18",
    currency: "JPY",
    quantity: 0,
    active: false,
  },
];

export const productLookups: LookupSet = {
  categories: makeLookup([
    { label: "Hardware", value: "cat-1" },
    { label: "Fasteners", value: "cat-2" },
    { label: "Twin", value: "cat-3" },
    { label: "twin", value: "cat-4" },
  ]),
};

export interface ProductFilters {
  search: string;
}

export function toProductPayload(row: ParsedRow, lookups: LookupSet): ProductPayload {
  const category = row.category;
  let categoryId: string | null = null;
  if (typeof category === "string") {
    const resolved = lookups.categories?.resolve(category);
    if (resolved === undefined) throw new RowProblem(`Category "${category}" was not found`);
    categoryId = resolved;
  }
  return {
    sku: String(row.sku),
    name: String(row.name),
    status: typeof row.status === "string" ? row.status : null,
    categoryId,
    priceMinor: typeof row.price === "number" ? row.price : null,
    currency: typeof row.currency === "string" ? row.currency : null,
    quantity: typeof row.quantity === "number" ? row.quantity : null,
    weight: typeof row.weight === "number" ? row.weight : null,
    launched: typeof row.launched === "string" ? row.launched : null,
    active: typeof row.active === "boolean" ? row.active : null,
    email: typeof row.email === "string" ? row.email : null,
    phone: typeof row.phone === "string" ? row.phone : null,
  };
}

export interface ProductUpdateOptions {
  /** The stored records the update matches rows against. */
  items?: readonly ProductItem[];
  load?: UpdateSpec<ProductItem>["loadExisting"];
  send?: (existing: ProductItem, body: Readonly<Record<string, unknown>>) => Promise<void>;
  extras?: UpdateSpec<ProductItem>["extras"];
  check?: UpdateSpec<ProductItem>["check"];
}

/**
 * How the test products are updated: matched by Record ID or SKU, SKU and currency never change,
 * price is converted in the stored currency, and only the e-mail can be emptied.
 */
export function makeProductUpdate(options: ProductUpdateOptions = {}): UpdateSpec<ProductItem> {
  const items = options.items ?? [];
  return {
    loadExisting: options.load ?? (() => Promise.resolve(items)),
    idOf: (item) => item.id,
    keyColumn: "sku",
    keyOf: (item) => item.sku,
    currencyOf: (item) => item.currency,
    fields: [
      { column: "sku", current: (item) => item.sku, immutable: true },
      { column: "name", current: (item) => item.name },
      { column: "status", current: (item) => item.status },
      { column: "price", field: "price_minor", current: (item) => item.priceMinor },
      { column: "currency", current: (item) => item.currency, immutable: true },
      { column: "quantity", current: (item) => item.quantity },
      { column: "active", field: "is_active", current: (item) => item.active },
      { column: "email", current: (item) => (item.email === "" ? null : item.email), clear: "" },
    ],
    // Set by a create only: a filled cell in one of these refuses an update row.
    notUpdatable: ["category", "weight", "launched", "phone"],
    send: options.send ?? (() => Promise.resolve()),
    ...(options.extras === undefined ? {} : { extras: options.extras }),
    ...(options.check === undefined ? {} : { check: options.check }),
  };
}

/** A stored product, with the values the tests compare a sheet against. */
export function storedProduct(i: number, overrides: Partial<ProductItem> = {}): ProductItem {
  return {
    id: `p-${i}`,
    sku: `SKU-${i}`,
    name: `Product ${i}`,
    status: "in_stock",
    category: "Hardware",
    priceMinor: 1000,
    currency: "INR",
    quantity: i,
    active: true,
    email: "orders@example.com",
    createdAt: "2026-09-26T10:30:00Z",
    ...overrides,
  };
}

export interface ProductEntityOptions {
  update?: ImportEntity<ProductPayload, ProductItem, ProductFilters>["update"];
  create?: ImportEntity<ProductPayload, ProductItem, ProductFilters>["create"];
  fetchPage?: NonNullable<ImportEntity<ProductPayload, ProductItem, ProductFilters>["export"]>["fetchPage"];
  maxRows?: number;
  exportMaxRows?: number;
  loadLookups?: ImportEntity<ProductPayload, ProductItem, ProductFilters>["loadLookups"];
  columns?: readonly ImportColumn[];
  sampleRows?: readonly Record<string, SheetValue>[];
  recordIdKey?: string;
}

export function makeProductEntity(
  options: ProductEntityOptions = {},
): ImportEntity<ProductPayload, ProductItem, ProductFilters> {
  return defineImportEntity<ProductPayload, ProductItem, ProductFilters>({
    id: "test.products",
    noun: "products",
    nounSingular: "product",
    fileStem: "products",
    columns: options.columns ?? productColumns,
    sampleRows: options.sampleRows ?? productSamples,
    maxRows: options.maxRows,
    recordIdKey: options.recordIdKey,
    update: options.update,
    notes: ["A SKU that already exists is skipped."],
    loadLookups: options.loadLookups ?? (() => Promise.resolve(productLookups)),
    toPayload: (row, ctx) => toProductPayload(row, ctx.lookups),
    create: options.create ?? (() => Promise.resolve({ id: "new-id" })),
    export: {
      maxRows: options.exportMaxRows,
      fetchPage:
        options.fetchPage ??
        (() => Promise.resolve({ items: [], total: 0, pages: 1 })),
      toRow: (item) => ({
        id: item.id,
        sku: item.sku,
        name: item.name,
        status: item.status,
        category: item.category,
        price: minorToMajor(item.priceMinor, item.currency),
        currency: item.currency,
        quantity: item.quantity,
        active: item.active,
        email: item.email,
        created: item.createdAt,
      }),
    },
  });
}

/** One column of an entity by key; a test that names a column that is not there should fail loudly. */
export function columnOf(entity: Pick<ImportEntity<unknown>, "columns">, key: string): ImportColumn {
  const found = entity.columns.find((column) => column.key === key);
  if (found === undefined) throw new Error(`No column "${key}"`);
  return found;
}

/** An empty workbook to build a test file in. */
export async function newWorkbook(): Promise<ExcelJS.Workbook> {
  const excel = await loadExcel();
  return new excel.Workbook();
}

/** The workbook as a `File`, the way the file input would hand it over. */
export async function fileOf(workbook: ExcelJS.Workbook, name = "import.xlsx"): Promise<File> {
  const bytes = await workbook.xlsx.writeBuffer();
  return new File([bytes], name, {
    type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  });
}

/** A workbook whose "Import" sheet holds `rows` under `headers`. */
export async function fileWithRows(
  headers: readonly string[],
  rows: readonly (readonly ExcelJS.CellValue[])[],
  name = "import.xlsx",
): Promise<File> {
  const workbook = await newWorkbook();
  const sheet = workbook.addWorksheet("Import");
  sheet.addRow([...headers]);
  for (const row of rows) sheet.addRow([...row]);
  return fileOf(workbook, name);
}

/** Loads a generated Blob back with the library, to inspect what was written. */
export async function loadBlob(blob: Blob): Promise<ExcelJS.Workbook> {
  const workbook = await newWorkbook();
  await workbook.xlsx.load(loadableBytes(await readBytes(blob)));
  return workbook;
}
