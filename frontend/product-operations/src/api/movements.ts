/**
 * Inventory Phase 3 client — transfers, the in-transit report, reservations, adjustments and the
 * approval thresholds (`services/operations/app/movements/api.py`, docs/inventory/movements.md).
 *
 * Same shape as `client.ts`: live through the gateway by default, `VITE_USE_FIXTURES=true` serves
 * the same calls from `movementFixtures.ts` in dev builds. Quantities are decimal strings on the
 * wire both ways. Every stock-moving write takes an `Idempotency-Key` the caller mints once per
 * distinct submission and re-sends on a retry, so a retry after a lost answer is a replay.
 *
 * Lists are keyset-paginated: `?cursor=&limit=` in, `{items, next_cursor}` out. Nothing here
 * re-sorts or re-filters a page the server already decided.
 */

import { apiRequest, settle } from "@opsgrids/client";
import { USE_FIXTURES } from "./client";
import {
  fixtureApproveTransfer,
  fixtureCancelAdjustment,
  fixtureCancelTransfer,
  fixtureCreateAdjustment,
  fixtureCreateReservation,
  fixtureCreateTransfer,
  fixtureDispatchTransfer,
  fixtureGetAdjustment,
  fixtureGetReservation,
  fixtureGetSettings,
  fixtureGetTransfer,
  fixtureInTransit,
  fixtureListAdjustments,
  fixtureListReservations,
  fixtureListTransfers,
  fixturePatchTransfer,
  fixturePutSettings,
  fixtureReceiveTransfer,
  fixtureRejectTransfer,
  fixtureReleaseReservation,
  fixtureRequestTransfer,
} from "./movementFixtures";
import type {
  AdjustmentCancelIn,
  AdjustmentIn,
  AdjustmentOut,
  AdjustmentStatus,
  ControlSettingsIn,
  ControlSettingsOut,
  CursorPage,
  InTransitRowOut,
  ReservationDetailOut,
  ReservationIn,
  ReservationReleaseIn,
  ReservationReleaseOut,
  ReservationStatus,
  TransferCancelIn,
  TransferDecisionIn,
  TransferDispatchIn,
  TransferIn,
  TransferOut,
  TransferPatch,
  TransferReceiveIn,
  TransferStatus,
  TransferSummaryOut,
} from "./movementTypes";

const BASE = "/operations/inventory";

function query(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === "") continue;
    search.set(key, String(value));
  }
  const text = search.toString();
  return text === "" ? "" : `?${text}`;
}

function idempotent(key: string): { headers: Record<string, string> } {
  return { headers: { "Idempotency-Key": key } };
}

const id = encodeURIComponent;

/* ------------------------------ control settings ------------------------------ */

/** `GET /operations/inventory/control-settings` — `operations.inventory.stock.read`. */
export async function getControlSettings(): Promise<ControlSettingsOut> {
  if (USE_FIXTURES) return settle(fixtureGetSettings());
  return apiRequest<ControlSettingsOut>(`${BASE}/control-settings`);
}

/** `PUT /operations/inventory/control-settings` — `operations.inventory.control.configure`.
 *  Replaces both thresholds; `null` clears one. */
export async function putControlSettings(input: ControlSettingsIn): Promise<ControlSettingsOut> {
  if (USE_FIXTURES) return settle(fixturePutSettings(input));
  return apiRequest<ControlSettingsOut>(`${BASE}/control-settings`, { method: "PUT", body: input });
}

/* ---------------------------------- transfers --------------------------------- */

export interface ListTransfersParams {
  status?: TransferStatus | undefined;
  from_warehouse_id?: string | undefined;
  to_warehouse_id?: string | undefined;
  item_id?: string | undefined;
  /** A transfer-number prefix, at most 30 characters. */
  q?: string | undefined;
  cursor?: string | undefined;
  limit?: number | undefined;
  signal?: AbortSignal | undefined;
}

/** `GET /operations/inventory/transfers` — `operations.inventory.stock.read`. Newest first. */
export async function listTransfers(params: ListTransfersParams = {}): Promise<CursorPage<TransferSummaryOut>> {
  if (USE_FIXTURES) return settle(fixtureListTransfers(params));
  const { signal, ...filters } = params;
  return apiRequest<CursorPage<TransferSummaryOut>>(`${BASE}/transfers${query(filters)}`, {
    ...(signal ? { signal } : {}),
  });
}

/** `GET /operations/inventory/transfers/{id}`. */
export async function getTransfer(transferId: string): Promise<TransferOut> {
  if (USE_FIXTURES) return settle(fixtureGetTransfer(transferId));
  return apiRequest<TransferOut>(`${BASE}/transfers/${id(transferId)}`);
}

/** `POST /operations/inventory/transfers` — `operations.inventory.transfer.write`. A DRAFT between
 *  two different warehouses. 201 created, 200 when the key replays an earlier create. */
export async function createTransfer(input: TransferIn, idempotencyKey: string): Promise<TransferOut> {
  if (USE_FIXTURES) return settle(fixtureCreateTransfer(input));
  return apiRequest<TransferOut>(`${BASE}/transfers`, { method: "POST", body: input, ...idempotent(idempotencyKey) });
}

/** `PATCH /operations/inventory/transfers/{id}` — `transfer.write`, a DRAFT only. */
export async function updateTransfer(transferId: string, patch: TransferPatch): Promise<TransferOut> {
  if (USE_FIXTURES) return settle(fixturePatchTransfer(transferId, patch));
  return apiRequest<TransferOut>(`${BASE}/transfers/${id(transferId)}`, { method: "PATCH", body: patch });
}

/** `POST …/transfers/{id}:request` — `transfer.write`, DRAFT. The server picks the approval route. */
export async function requestTransfer(transferId: string): Promise<TransferOut> {
  if (USE_FIXTURES) return settle(fixtureRequestTransfer(transferId));
  return apiRequest<TransferOut>(`${BASE}/transfers/${id(transferId)}:request`, { method: "POST" });
}

/** `POST …/transfers/{id}:approve` — `transfer.approve`, REQUESTED on the DIRECT route, and never
 *  by the requester (409). */
export async function approveTransfer(transferId: string, input: TransferDecisionIn = {}): Promise<TransferOut> {
  if (USE_FIXTURES) return settle(fixtureApproveTransfer(transferId, input));
  return apiRequest<TransferOut>(`${BASE}/transfers/${id(transferId)}:approve`, { method: "POST", body: input });
}

/** `POST …/transfers/{id}:reject` — as approve; the comment says why and is required (422). */
export async function rejectTransfer(transferId: string, input: TransferDecisionIn): Promise<TransferOut> {
  if (USE_FIXTURES) return settle(fixtureRejectTransfer(transferId, input));
  return apiRequest<TransferOut>(`${BASE}/transfers/${id(transferId)}:reject`, { method: "POST", body: input });
}

/** `POST …/transfers/{id}:cancel` — `transfer.write`, before dispatch only. */
export async function cancelTransfer(transferId: string, input: TransferCancelIn): Promise<TransferOut> {
  if (USE_FIXTURES) return settle(fixtureCancelTransfer(transferId, input));
  return apiRequest<TransferOut>(`${BASE}/transfers/${id(transferId)}:cancel`, { method: "POST", body: input });
}

/** `POST …/transfers/{id}:dispatch` — `transfer.dispatch`, APPROVED. Stock leaves the source for
 *  in-transit; 409 `insufficient-stock` when the source no longer has it. */
export async function dispatchTransfer(
  transferId: string,
  input: TransferDispatchIn,
  idempotencyKey: string,
): Promise<TransferOut> {
  if (USE_FIXTURES) return settle(fixtureDispatchTransfer(transferId, input));
  return apiRequest<TransferOut>(`${BASE}/transfers/${id(transferId)}:dispatch`, {
    method: "POST",
    body: input,
    ...idempotent(idempotencyKey),
  });
}

/** `POST …/transfers/{id}:receive` — `transfer.receive`, IN_TRANSIT. May be called repeatedly until
 *  every line is settled; a shortage is declared lost in transit and written off. */
export async function receiveTransfer(
  transferId: string,
  input: TransferReceiveIn,
  idempotencyKey: string,
): Promise<TransferOut> {
  if (USE_FIXTURES) return settle(fixtureReceiveTransfer(transferId, input));
  return apiRequest<TransferOut>(`${BASE}/transfers/${id(transferId)}:receive`, {
    method: "POST",
    body: input,
    ...idempotent(idempotencyKey),
  });
}

/** `GET /operations/inventory/in-transit` — stock on its way, by item and destination, with the
 *  destination's physical on-hand beside it (never added to it). */
export async function listInTransit(
  params: { warehouse_id?: string | undefined; item_id?: string | undefined } = {},
): Promise<InTransitRowOut[]> {
  if (USE_FIXTURES) return settle(fixtureInTransit(params));
  return apiRequest<InTransitRowOut[]>(`${BASE}/in-transit${query(params)}`);
}

/* -------------------------------- reservations -------------------------------- */

export interface ListReservationsParams {
  status?: ReservationStatus | undefined;
  reference_type?: string | undefined;
  item_id?: string | undefined;
  location_id?: string | undefined;
  reference_id?: string | undefined;
  expiring_before?: string | undefined;
  cursor?: string | undefined;
  limit?: number | undefined;
  signal?: AbortSignal | undefined;
}

/** `GET /operations/inventory/reservations` — `operations.inventory.stock.read`. */
export async function listReservations(
  params: ListReservationsParams = {},
): Promise<CursorPage<ReservationDetailOut>> {
  if (USE_FIXTURES) return settle(fixtureListReservations(params));
  const { signal, ...filters } = params;
  return apiRequest<CursorPage<ReservationDetailOut>>(`${BASE}/reservations${query(filters)}`, {
    ...(signal ? { signal } : {}),
  });
}

/** `GET /operations/inventory/reservations/{id}`. */
export async function getReservation(reservationId: string): Promise<ReservationDetailOut> {
  if (USE_FIXTURES) return settle(fixtureGetReservation(reservationId));
  return apiRequest<ReservationDetailOut>(`${BASE}/reservations/${id(reservationId)}`);
}

/** `POST /operations/inventory/reservations` — `operations.inventory.stock.reserve`. 409
 *  `insufficient-stock` carries requested / on_hand / reserved / available and the reservations in
 *  the way. */
export async function createReservation(input: ReservationIn, idempotencyKey: string): Promise<ReservationDetailOut> {
  if (USE_FIXTURES) return settle(fixtureCreateReservation(input));
  return apiRequest<ReservationDetailOut>(`${BASE}/reservations`, {
    method: "POST",
    body: input,
    ...idempotent(idempotencyKey),
  });
}

/** `POST …/reservations/{id}:release` — `operations.inventory.stock.reserve`. An empty body gives
 *  back the whole open remainder; `quantity` gives back only that much. */
export async function releaseReservation(
  reservationId: string,
  input: ReservationReleaseIn,
  idempotencyKey: string,
): Promise<ReservationReleaseOut> {
  if (USE_FIXTURES) return settle(fixtureReleaseReservation(reservationId, input));
  return apiRequest<ReservationReleaseOut>(`${BASE}/reservations/${id(reservationId)}:release`, {
    method: "POST",
    body: input,
    ...idempotent(idempotencyKey),
  });
}

/* --------------------------------- adjustments -------------------------------- */

export interface ListAdjustmentsParams {
  status?: AdjustmentStatus | undefined;
  item_id?: string | undefined;
  cursor?: string | undefined;
  limit?: number | undefined;
  signal?: AbortSignal | undefined;
}

/**
 * `POST /operations/inventory/adjustments` — `operations.inventory.stock.adjust`.
 *
 * The server answers 201 when the adjustment was applied and 202 when it is held for approval
 * (200 for a replayed key). Those are the same fact as the body's `status` — `APPLIED` versus
 * `PENDING_APPROVAL` — and the client reads that, since `apiRequest` hands back the body alone.
 * A held adjustment must carry `evidence_document_id`; without it the answer is a 422 on that field.
 */
export async function createAdjustment(input: AdjustmentIn, idempotencyKey: string): Promise<AdjustmentOut> {
  if (USE_FIXTURES) return settle(fixtureCreateAdjustment(input));
  return apiRequest<AdjustmentOut>(`${BASE}/adjustments`, {
    method: "POST",
    body: input,
    ...idempotent(idempotencyKey),
  });
}

/** `GET /operations/inventory/adjustments` — `operations.inventory.stock.read`. */
export async function listAdjustments(params: ListAdjustmentsParams = {}): Promise<CursorPage<AdjustmentOut>> {
  if (USE_FIXTURES) return settle(fixtureListAdjustments(params));
  const { signal, ...filters } = params;
  return apiRequest<CursorPage<AdjustmentOut>>(`${BASE}/adjustments${query(filters)}`, {
    ...(signal ? { signal } : {}),
  });
}

/** `GET /operations/inventory/adjustments/{id}`. */
export async function getAdjustment(adjustmentId: string): Promise<AdjustmentOut> {
  if (USE_FIXTURES) return settle(fixtureGetAdjustment(adjustmentId));
  return apiRequest<AdjustmentOut>(`${BASE}/adjustments/${id(adjustmentId)}`);
}

/** `POST …/adjustments/{id}:cancel` — `operations.inventory.stock.adjust`; only while
 *  `PENDING_APPROVAL`. */
export async function cancelAdjustment(adjustmentId: string, input: AdjustmentCancelIn = {}): Promise<AdjustmentOut> {
  if (USE_FIXTURES) return settle(fixtureCancelAdjustment(adjustmentId, input));
  return apiRequest<AdjustmentOut>(`${BASE}/adjustments/${id(adjustmentId)}:cancel`, {
    method: "POST",
    body: input,
  });
}
