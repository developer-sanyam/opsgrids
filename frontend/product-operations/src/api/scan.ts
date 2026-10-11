/**
 * Scan, stock-operation, bulk import and export calls (inventory Phase 9, ADR-0027).
 *
 * Live only — these endpoints have no fixture mode: a scan resolves against real labels, and an
 * import that "succeeds" against fixtures would teach nobody anything. Every quantity is a
 * decimal string, every write that moves stock carries an `Idempotency-Key`.
 */

import { apiRequest } from "@opsgrids/client";
import type { ScanType } from "@/lib/scanPayload";

export type ScanAction =
  | "view"
  | "transfer"
  | "status_change"
  | "count"
  | "receive"
  | "pick"
  | "issue"
  | "inspect";

export interface ScanEntityOut {
  type: ScanType;
  id: string;
  code: string;
  title: string;
  subtitle: string;
  uom: string | null;
  on_hand_qty: string | null;
  inventory_item_id: string | null;
  sku: string | null;
  lot_id: string | null;
  lot_no: string | null;
  roll_id: string | null;
  location_id: string | null;
  warehouse_id: string | null;
  track_lot: boolean;
  track_roll: boolean;
  status: string | null;
}

export interface ScanOut {
  payload: string;
  type: ScanType;
  entity: ScanEntityOut;
  allowed_actions: ScanAction[];
}

/**
 * `GET /operations/inventory/scan?code=` — `operations.inventory.stock.read`. The code travels
 * in the query so `/..`, `?`, `#` and `%` in a lot number survive the gateway's path handling.
 */
export function resolveScan(payload: string, signal?: AbortSignal): Promise<ScanOut> {
  return apiRequest<ScanOut>(`/operations/inventory/scan?code=${encodeURIComponent(payload)}`, {
    ...(signal ? { signal } : {}),
  });
}

// ------------------------------------------------------------------ stock buckets and moves

export type OnSiteStatus =
  | "AVAILABLE"
  | "QUALITY_HOLD"
  | "QUARANTINE"
  | "DAMAGED"
  | "REJECTED"
  | "WIP"
  | "BLOCKED";

export interface StockRow {
  id: string;
  inventory_item_id: string;
  sku: string;
  item_name: string;
  uom: string;
  warehouse_id: string;
  warehouse_code: string;
  location_id: string;
  location_code: string;
  status: string;
  lot_id: string | null;
  lot_no: string | null;
  roll_id: string | null;
  roll_no: string | null;
  on_hand_qty: string;
  reserved_qty: string;
  available_qty: string;
}

interface CursorPage<T> {
  items: T[];
  next_cursor: string | null;
}

/** The buckets a scanned thing sits in — `GET /operations/inventory/stock`. */
export async function listStockFor(entity: ScanEntityOut): Promise<StockRow[]> {
  const params = new URLSearchParams({ limit: "100" });
  if (entity.type === "roll" && entity.roll_id) params.set("roll_id", entity.roll_id);
  else if (entity.type === "lot" && entity.lot_id) params.set("lot_id", entity.lot_id);
  else if (entity.type === "item" && entity.inventory_item_id) {
    params.set("item_id", entity.inventory_item_id);
  } else if (entity.type === "location" && entity.location_id) {
    params.set("location_id", entity.location_id);
  }
  const page = await apiRequest<CursorPage<StockRow>>(`/operations/inventory/stock?${params}`);
  return page.items;
}

export interface StockMoveOut {
  move_id: string;
  kind: string;
  replayed: boolean;
}

export interface StockTransferIn {
  inventory_item_id: string;
  from_location_id: string;
  to_location_id: string;
  quantity: string;
  status: OnSiteStatus;
  lot_id: string | null;
  roll_id: string | null;
  note?: string;
}

/** `POST /operations/inventory/stock/transfers` — bin to bin inside one warehouse. */
export function transferStock(input: StockTransferIn, idempotencyKey: string): Promise<StockMoveOut> {
  return apiRequest<StockMoveOut>("/operations/inventory/stock/transfers", {
    method: "POST",
    body: input,
    headers: { "Idempotency-Key": idempotencyKey },
  });
}

export interface StockStatusChangeIn {
  inventory_item_id: string;
  location_id: string;
  from_status: OnSiteStatus;
  to_status: OnSiteStatus;
  quantity: string;
  lot_id: string | null;
  roll_id: string | null;
  note?: string;
}

/** `POST /operations/inventory/stock/status-changes` — e.g. AVAILABLE -> QUALITY_HOLD. */
export function changeStockStatus(
  input: StockStatusChangeIn,
  idempotencyKey: string,
): Promise<StockMoveOut> {
  return apiRequest<StockMoveOut>("/operations/inventory/stock/status-changes", {
    method: "POST",
    body: input,
    headers: { "Idempotency-Key": idempotencyKey },
  });
}

// ------------------------------------------------------------------ bulk import / export

export type ImportEntity = "items" | "locations" | "lots" | "rolls" | "opening_stock";
export type ExportEntity = "items" | "locations" | "lots" | "rolls" | "stock" | "ledger";
export type ImportMode = "preview" | "commit";
export type OnDuplicate = "fail" | "skip";

export interface ImportColumn {
  name: string;
  required: boolean;
  description: string;
  example: string;
}

export interface ImportTemplate {
  entity: ImportEntity;
  max_rows: number;
  columns: ImportColumn[];
}

export interface ImportRowError {
  row: number;
  field: string | null;
  code: string;
  message: string;
}

export interface ImportRowResult {
  row: number;
  status: "ok" | "skipped" | "error";
  key: string | null;
}

export interface ImportResult {
  entity: ImportEntity;
  mode: ImportMode;
  committed: boolean;
  replayed: boolean;
  run_id: string | null;
  total: number;
  created: number;
  skipped: number;
  error_count: number;
  errors: ImportRowError[];
  rows: ImportRowResult[];
}

export type ImportCells = Record<string, string | null>;

/** `GET /operations/inventory/data/import/{entity}/template`. */
export function getImportTemplate(entity: ImportEntity): Promise<ImportTemplate> {
  return apiRequest<ImportTemplate>(`/operations/inventory/data/import/${entity}/template`);
}

export interface ImportRequest {
  entity: ImportEntity;
  mode: ImportMode;
  onDuplicate: OnDuplicate;
  rows: ImportCells[];
  sourceName?: string;
}

/**
 * `POST /operations/inventory/data/import/{entity}` — a preview writes nothing; a commit is
 * all-or-nothing and needs the `Idempotency-Key`. A refused commit is a 422 `ApiError` whose
 * problem document carries every row error in `errors[]`.
 */
export function runImport(input: ImportRequest, idempotencyKey?: string): Promise<ImportResult> {
  return apiRequest<ImportResult>(`/operations/inventory/data/import/${input.entity}`, {
    method: "POST",
    body: {
      mode: input.mode,
      on_duplicate: input.onDuplicate,
      rows: input.rows,
      ...(input.sourceName ? { source_name: input.sourceName } : {}),
    },
    ...(idempotencyKey ? { headers: { "Idempotency-Key": idempotencyKey } } : {}),
  });
}

export interface ExportOut {
  entity: ExportEntity;
  columns: string[];
  rows: Record<string, string>[];
  truncated: boolean;
}

/** `GET /operations/inventory/data/export/{entity}` — `data.export` plus the list's read right. */
export function exportData(entity: ExportEntity): Promise<ExportOut> {
  return apiRequest<ExportOut>(`/operations/inventory/data/export/${entity}`);
}

// ------------------------------------------------------------------ lists the label views print

export interface LabelItem {
  type: ScanType;
  id: string;
  /** What the label encodes: the printed barcode if the record has one, else its natural key. */
  code: string;
  title: string;
  subtitle: string;
}

interface LotListRow {
  id: string;
  lot_no: string;
  barcode?: string | null;
  sku: string;
}
interface RollListRow {
  id: string;
  roll_no: string;
  barcode?: string | null;
  sku: string;
  lot_no: string | null;
  primary_qty: string;
  uom: string;
}
interface ItemListRow {
  id: string;
  sku: string;
  name: string;
  barcode?: string | null;
}
interface ItemPage {
  items: ItemListRow[];
}
interface LocationListRow {
  id: string;
  code: string;
  name: string;
  barcode: string | null;
  kind: string;
}

/** Up to `limit` printable records of one kind, filtered by the server's own `q`. */
export async function listLabelItems(
  type: ScanType,
  options: { q?: string; warehouseId?: string; limit?: number } = {},
): Promise<LabelItem[]> {
  const limit = options.limit ?? 100;
  const q = options.q ? `&q=${encodeURIComponent(options.q)}` : "";
  if (type === "item") {
    const page = await apiRequest<ItemPage>(`/operations/inventory?size=${limit}&page=1${q}`);
    return page.items.map((i) => ({
      type,
      id: i.id,
      code: i.barcode || i.sku,
      title: i.name,
      subtitle: i.sku,
    }));
  }
  if (type === "lot") {
    const page = await apiRequest<CursorPage<LotListRow>>(
      `/operations/inventory/lots?limit=${limit}${q}`,
    );
    return page.items.map((l) => ({
      type,
      id: l.id,
      code: l.barcode || l.lot_no,
      title: l.lot_no,
      subtitle: l.sku,
    }));
  }
  if (type === "roll") {
    const page = await apiRequest<CursorPage<RollListRow>>(
      `/operations/inventory/rolls?limit=${limit}${q}`,
    );
    return page.items.map((r) => ({
      type,
      id: r.id,
      code: r.barcode || r.roll_no,
      title: r.roll_no,
      subtitle: `${r.sku}${r.lot_no ? ` · ${r.lot_no}` : ""} · ${r.primary_qty} ${r.uom}`,
    }));
  }
  if (!options.warehouseId) return [];
  const rows = await apiRequest<LocationListRow[] | { items: LocationListRow[] }>(
    `/operations/warehouses/${encodeURIComponent(options.warehouseId)}/locations?size=${limit}&page=1`,
  );
  const list = Array.isArray(rows) ? rows : rows.items;
  return list
    .filter((l) => l.barcode)
    .map((l) => ({
      type,
      id: l.id,
      code: l.barcode as string,
      title: l.name || l.code,
      subtitle: `${l.kind} · ${l.code}`,
    }));
}
