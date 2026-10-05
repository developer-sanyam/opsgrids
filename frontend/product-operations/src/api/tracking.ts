/**
 * Inventory Phase 1 calls: lots, rolls, stock operations, and the stock / ledger / trace reads.
 *
 * Always live. These endpoints have no fixture twin: a lot's genealogy or a ledger page invented
 * in memory would be a second, unreviewed implementation of what the ledger computes under a row
 * lock, and fixture mode exists to design layouts, not to simulate a posting kernel. In fixture
 * mode these screens show their error state.
 *
 * Every write takes an `Idempotency-Key` as a required argument, so a call site cannot forget one:
 * the stock-moving routes (opening, transfer, status change, split, merge) replay a retried key
 * instead of posting twice. Lot and roll registration and location delete do not read the header
 * today; it is sent anyway so a later server-side replay needs no client change.
 *
 * Quantities are decimal strings both ways (ADR-0014 §2) and are never parsed into floats here.
 */

import { apiRequest } from "@opsgrids/client";
import type {
  CursorPage,
  LedgerKind,
  LedgerRowOut,
  LotGenealogyOut,
  LotIn,
  LotOut,
  LotQualityStatus,
  LocateOut,
  LotTraceOut,
  OpeningStockIn,
  PutawayDetailOut,
  PutawayRevealOut,
  RollEventOut,
  RollIn,
  RollMergeIn,
  RollMergeOut,
  RollOut,
  RollSplitIn,
  RollSplitOut,
  RollState,
  RollTraceOut,
  StockMoveOut,
  StockRowOut,
  StockStatusChangeIn,
  StockTransferIn,
} from "./trackingTypes";
import type { StockStatus } from "./types";

const BASE = "/operations/inventory";

/** Paging shared by every keyset read: the opaque cursor from the previous page, and a page size. */
export interface CursorParams {
  cursor?: string | undefined;
  limit?: number | undefined;
  signal?: AbortSignal | undefined;
}

type QueryValue = string | number | boolean | undefined;

function query(params: Record<string, QueryValue>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === "") continue;
    search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

function idempotent(key: string): { headers: Record<string, string> } {
  return { headers: { "Idempotency-Key": key } };
}

const id = encodeURIComponent;

/* ----------------------------------- lots ---------------------------------- */

export interface ListLotsParams extends CursorParams {
  item_id?: string | undefined;
  quality_status?: LotQualityStatus | undefined;
  /** Lot number prefix. */
  q?: string | undefined;
}

/** `GET /operations/inventory/lots` — `operations.inventory.stock.read`. Newest first. */
export function listLots(params: ListLotsParams = {}): Promise<CursorPage<LotOut>> {
  const { signal, ...filters } = params;
  return apiRequest<CursorPage<LotOut>>(`${BASE}/lots${query(filters)}`, { signal });
}

export function getLot(lotId: string): Promise<LotOut> {
  return apiRequest<LotOut>(`${BASE}/lots/${id(lotId)}`);
}

/** `GET /operations/inventory/lots/{id}/trace` — the lot, every lot it came from, and everything
 *  that came of it. */
export function getLotTrace(lotId: string): Promise<LotTraceOut> {
  return apiRequest<LotTraceOut>(`${BASE}/lots/${id(lotId)}/trace`);
}

/** `GET /operations/inventory/lots/{id}/genealogy` — the edges touching the lot, newest first. */
export function listLotGenealogy(lotId: string, params: CursorParams = {}): Promise<CursorPage<LotGenealogyOut>> {
  const { signal, ...paging } = params;
  return apiRequest<CursorPage<LotGenealogyOut>>(`${BASE}/lots/${id(lotId)}/genealogy${query(paging)}`, {
    signal,
  });
}

/** `POST /operations/inventory/lots` — `operations.inventory.lot.write`. 409 on a duplicate lot
 *  number; 422 when the item is not lot-tracked. */
export function createLot(input: LotIn, idempotencyKey: string): Promise<LotOut> {
  return apiRequest<LotOut>(`${BASE}/lots`, { method: "POST", body: input, ...idempotent(idempotencyKey) });
}

/* ----------------------------------- rolls --------------------------------- */

export interface ListRollsParams extends CursorParams {
  item_id?: string | undefined;
  lot_id?: string | undefined;
  state?: RollState | undefined;
  shade?: string | undefined;
  grade?: string | undefined;
  /** Roll number prefix. */
  q?: string | undefined;
}

/** `GET /operations/inventory/rolls` — `operations.inventory.stock.read`. Newest first. */
export function listRolls(params: ListRollsParams = {}): Promise<CursorPage<RollOut>> {
  const { signal, ...filters } = params;
  return apiRequest<CursorPage<RollOut>>(`${BASE}/rolls${query(filters)}`, { signal });
}

export function getRoll(rollId: string): Promise<RollOut> {
  return apiRequest<RollOut>(`${BASE}/rolls/${id(rollId)}`);
}

/** `GET /operations/inventory/rolls/{id}/trace` — parents, children, the lot and the lot's origin. */
export function getRollTrace(rollId: string): Promise<RollTraceOut> {
  return apiRequest<RollTraceOut>(`${BASE}/rolls/${id(rollId)}/trace`);
}

/** `GET /operations/inventory/rolls/{id}/events` — a roll's history (split, merge, moves), newest first. */
export function listRollEvents(rollId: string, params: CursorParams = {}): Promise<CursorPage<RollEventOut>> {
  const { signal, ...paging } = params;
  return apiRequest<CursorPage<RollEventOut>>(`${BASE}/rolls/${id(rollId)}/events${query(paging)}`, {
    signal,
  });
}

/** `POST /operations/inventory/rolls` — `operations.inventory.roll.write`. The roll holds no stock
 *  until an opening posts into it. */
export function createRoll(input: RollIn, idempotencyKey: string): Promise<RollOut> {
  return apiRequest<RollOut>(`${BASE}/rolls`, { method: "POST", body: input, ...idempotent(idempotencyKey) });
}

/** `POST /operations/inventory/rolls/{id}:split` — `operations.inventory.roll.write`. The children's
 *  quantities must add up exactly to what the roll's bucket holds. */
export function splitRoll(rollId: string, input: RollSplitIn, idempotencyKey: string): Promise<RollSplitOut> {
  return apiRequest<RollSplitOut>(`${BASE}/rolls/${id(rollId)}:split`, {
    method: "POST",
    body: input,
    ...idempotent(idempotencyKey),
  });
}

/** `POST /operations/inventory/rolls:merge` — `operations.inventory.roll.write`. Same item, lot and
 *  shade, all active, all holding stock in the named bucket. */
export function mergeRolls(input: RollMergeIn, idempotencyKey: string): Promise<RollMergeOut> {
  return apiRequest<RollMergeOut>(`${BASE}/rolls:merge`, {
    method: "POST",
    body: input,
    ...idempotent(idempotencyKey),
  });
}

/* ------------------------------ stock operations --------------------------- */

/** `POST /operations/inventory/stock/opening` — `operations.inventory.stock.adjust`. */
export function postOpeningStock(input: OpeningStockIn, idempotencyKey: string): Promise<StockMoveOut> {
  return apiRequest<StockMoveOut>(`${BASE}/stock/opening`, {
    method: "POST",
    body: input,
    ...idempotent(idempotencyKey),
  });
}

/** `POST /operations/inventory/stock/transfers` — `operations.inventory.stock.transfer`. Bin to bin,
 *  one warehouse, immediate. */
export function transferStock(input: StockTransferIn, idempotencyKey: string): Promise<StockMoveOut> {
  return apiRequest<StockMoveOut>(`${BASE}/stock/transfers`, {
    method: "POST",
    body: input,
    ...idempotent(idempotencyKey),
  });
}

/** `POST /operations/inventory/stock/status-changes` — `operations.inventory.stock.transfer`. */
export function changeStockStatus(input: StockStatusChangeIn, idempotencyKey: string): Promise<StockMoveOut> {
  return apiRequest<StockMoveOut>(`${BASE}/stock/status-changes`, {
    method: "POST",
    body: input,
    ...idempotent(idempotencyKey),
  });
}

/** `DELETE /operations/locations/{id}` — `operations.inventory.location.write`. 409 with the reason
 *  while the location holds stock, has ledger history, is a default bin, has children or is an
 *  item's home bin. */
export async function deleteLocation(locationId: string, idempotencyKey: string): Promise<void> {
  await apiRequest<undefined>(`/operations/locations/${id(locationId)}`, {
    method: "DELETE",
    ...idempotent(idempotencyKey),
  });
}

/* ----------------------------------- reads --------------------------------- */

export interface ListStockParams extends CursorParams {
  item_id?: string | undefined;
  warehouse_id?: string | undefined;
  location_id?: string | undefined;
  lot_id?: string | undefined;
  roll_id?: string | undefined;
  status?: StockStatus | undefined;
  include_empty?: boolean | undefined;
}

/** `GET /operations/inventory/stock` — buckets of stock, `operations.inventory.stock.read`. */
export function listStock(params: ListStockParams = {}): Promise<CursorPage<StockRowOut>> {
  const { signal, ...filters } = params;
  return apiRequest<CursorPage<StockRowOut>>(`${BASE}/stock${query(filters)}`, { signal });
}

export interface ListLedgerParams extends CursorParams {
  item_id?: string | undefined;
  location_id?: string | undefined;
  lot_id?: string | undefined;
  roll_id?: string | undefined;
  kind?: LedgerKind | undefined;
  status?: StockStatus | undefined;
  /** Inclusive, on the move's business date (`yyyy-mm-dd`). */
  from_date?: string | undefined;
  to_date?: string | undefined;
}

/** `GET /operations/inventory/ledger` — the organization-wide ledger, newest first. */
export function listLedger(params: ListLedgerParams = {}): Promise<CursorPage<LedgerRowOut>> {
  const { signal, ...filters } = params;
  return apiRequest<CursorPage<LedgerRowOut>>(`${BASE}/ledger${query(filters)}`, { signal });
}

/* ------------------------- find stock and storage put-away ------------------------ */

export interface LocateParams {
  /** Required: the server answers 422 without it. */
  inventory_item_id: string;
  lot_id?: string | undefined;
  roll_id?: string | undefined;
  warehouse_id?: string | undefined;
  signal?: AbortSignal | undefined;
}

/** `GET /operations/inventory/locate` — `operations.inventory.stock.read`. The bins that hold a
 *  positive balance of the item (narrowed to a lot or roll), each with its floor-plan placement or
 *  the reason it has none. Geometry arrives only for a caller who also holds
 *  `operations.warehouse.layout.read`. */
export function locateStock(params: LocateParams): Promise<LocateOut> {
  const { signal, ...filters } = params;
  return apiRequest<LocateOut>(`${BASE}/locate${query(filters)}`, { signal });
}

/** `GET /operations/inventory/putaway/{move_id}` — `operations.inventory.stock.read`. 404 when the
 *  move has no capture, including one purged after its retention window. No coordinates. */
export function getPutawayDetail(moveId: string, params: { signal?: AbortSignal | undefined } = {}): Promise<PutawayDetailOut> {
  return apiRequest<PutawayDetailOut>(`${BASE}/putaway/${id(moveId)}`, { signal: params.signal });
}

/**
 * `POST /operations/inventory/putaway/{move_id}/location:reveal` —
 * `operations.inventory.putaway.location_read`, held by the operations admin role only. A POST on
 * purpose: every successful call writes an audit row, and a GET is cacheable, prefetchable and
 * logged in URLs. The response is `no-store`; keep it in memory only.
 */
export function revealPutawayLocation(moveId: string): Promise<PutawayRevealOut> {
  return apiRequest<PutawayRevealOut>(`${BASE}/putaway/${id(moveId)}/location:reveal`, { method: "POST" });
}
