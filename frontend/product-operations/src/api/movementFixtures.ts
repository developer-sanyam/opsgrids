/**
 * Fixture mode for the movement screens (transfers, reservations, adjustments, thresholds).
 *
 * The same rules the server applies, small enough to read: the transfer state machine, the
 * threshold routing, the available-stock check on a reservation, the evidence an over-threshold
 * adjustment needs. A refusal is an `ApiError` carrying a problem document, so a screen reads a
 * fixture failure exactly as it reads a live one. Quantities stay decimal strings throughout and
 * are only ever compared through exact integer milli-units.
 */

import { ApiError } from "@opsgrids/client";
import type { ProblemJson } from "@opsgrids/client";
import { milliToQty, qtyToMilli } from "@/lib/quantity";
import { INVENTORY, LOCATIONS, WAREHOUSES } from "./fixtures";
import type {
  AdjustmentCancelIn,
  AdjustmentIn,
  AdjustmentOut,
  AdjustmentStatus,
  ApprovalRoute,
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
  TransferLineOut,
  TransferOut,
  TransferPatch,
  TransferReceiveIn,
  TransferStatus,
  TransferSummaryOut,
  WarehouseRefOut,
} from "./movementTypes";
import type { InventoryItemOut, LocationOut, Qty } from "./types";

/** Who the fixture session is — the requester of a transfer cannot approve it. */
export const FIXTURE_ACTOR = "Asha Rao";

const PAGE = 25;

function problem(status: number, detail: string, extra: Partial<ProblemJson> = {}): ApiError {
  const slug = status === 404 ? "not-found" : status === 409 ? "conflict" : "validation";
  const body: ProblemJson = {
    type: `https://errors.opsgrids.dev/${slug}`,
    title: status === 404 ? "Not found" : status === 409 ? "Conflict" : "Validation failed",
    status,
    detail,
    ...extra,
  };
  return new ApiError(status, detail, body);
}

function fieldProblem(field: string, code: string, detail: string): ApiError {
  return problem(422, detail, { errors: [{ field, code, message: detail }] });
}

function paged<T>(rows: T[], cursor: string | undefined, limit: number | undefined): CursorPage<T> {
  const start = cursor === undefined ? 0 : Number(cursor);
  const size = limit ?? PAGE;
  const end = start + size;
  return { items: rows.slice(start, end), next_cursor: end < rows.length ? String(end) : null };
}

function clone<T>(value: T): T {
  return structuredClone(value);
}

const NOW = "2026-10-05T09:00:00Z";

function stamp(): string {
  return new Date().toISOString();
}

function number(prefix: string, n: number): string {
  return `${prefix}-${String(n).padStart(6, "0")}`;
}

/* ------------------------------- reference data ------------------------------- */

function warehouseRef(id: string): WarehouseRefOut {
  const found = WAREHOUSES.find((w) => w.id === id);
  if (!found) throw problem(404, `Warehouse ${id} is not in this organization`);
  return { id: found.id, code: found.code, name: found.name };
}

function item(id: string): InventoryItemOut {
  const found = INVENTORY.find((i) => i.id === id);
  if (!found) throw problem(404, `Item ${id} is not in this organization`);
  return found;
}

function locationOf(id: string): LocationOut {
  for (const list of Object.values(LOCATIONS)) {
    const found = list.find((l) => l.id === id);
    if (found) return found;
  }
  throw problem(404, `Location ${id} is not in this organization`);
}

function defaultBin(warehouseId: string): LocationOut | undefined {
  return (LOCATIONS[warehouseId] ?? []).find((l) => l.is_default);
}

/* --------------------------------- settings ---------------------------------- */

let settings: ControlSettingsOut = {
  transfer_approval_threshold: "500",
  adjustment_approval_threshold: "50",
  updated_at: NOW,
  updated_by: FIXTURE_ACTOR,
};

export function fixtureGetSettings(): ControlSettingsOut {
  return clone(settings);
}

function thresholdOrRefuse(value: Qty | null, field: string): Qty | null {
  if (value === null) return null;
  if (!/^\d+(\.\d{1,3})?$/.test(value)) {
    throw fieldProblem(field, "invalid_quantity", "Enter a number with at most 3 decimal places.");
  }
  if (qtyToMilli(value) <= 0) {
    throw fieldProblem(field, "invalid_quantity", "A threshold must be greater than zero (clear it to set none)");
  }
  return value;
}

export function fixturePutSettings(input: ControlSettingsIn): ControlSettingsOut {
  settings = {
    transfer_approval_threshold: thresholdOrRefuse(input.transfer_approval_threshold, "transfer_approval_threshold"),
    adjustment_approval_threshold: thresholdOrRefuse(
      input.adjustment_approval_threshold,
      "adjustment_approval_threshold",
    ),
    updated_at: stamp(),
    updated_by: FIXTURE_ACTOR,
  };
  return clone(settings);
}

/* --------------------------------- transfers --------------------------------- */

const BHIWANDI = WAREHOUSES[0];
const PUNE = WAREHOUSES[1];
const BINS = BHIWANDI ? (LOCATIONS[BHIWANDI.id] ?? []).filter((l) => l.kind === "bin") : [];

function makeLine(
  lineNo: number,
  itemIndex: number,
  quantity: Qty,
  progress: { dispatched?: Qty; received?: Qty; shortage?: Qty } = {},
): TransferLineOut {
  const inv = INVENTORY[itemIndex];
  const from = BINS[0];
  if (!inv || !from) throw new Error("fixture inventory is missing");
  const dispatched = progress.dispatched ?? "0";
  const received = progress.received ?? "0";
  const shortage = progress.shortage ?? "0";
  return {
    id: `trl-${lineNo}-${inv.id}`,
    line_no: lineNo,
    inventory_item_id: inv.id,
    sku: inv.sku,
    item_name: inv.name,
    uom: inv.uom,
    from_location_id: from.id,
    from_location_code: from.code,
    to_location_id: null,
    in_transit_location_id: null,
    lot_id: null,
    roll_id: null,
    quantity,
    dispatched,
    received,
    shortage,
    in_transit: milliToQty(qtyToMilli(dispatched) - qtyToMilli(received) - qtyToMilli(shortage)),
  };
}

function summaryOf(t: TransferOut): TransferSummaryOut {
  return {
    id: t.id,
    number: t.number,
    status: t.status,
    from_warehouse: t.from_warehouse,
    to_warehouse: t.to_warehouse,
    line_count: t.lines.length,
    approval_route: t.approval_route,
    expected_on: t.expected_on,
    requested_at: t.requested_at,
    dispatched_at: t.dispatched_at,
    completed_at: t.completed_at,
    created_at: t.created_at,
  };
}

function seedTransfer(
  n: number,
  status: TransferStatus,
  route: ApprovalRoute | null,
  lines: TransferLineOut[],
  extra: Partial<TransferOut> = {},
): TransferOut {
  if (!BHIWANDI || !PUNE) throw new Error("fixture warehouses are missing");
  const created = `2026-10-0${n}T08:00:00Z`;
  return {
    id: `trf-${n}`,
    number: number("TRF", n),
    status,
    from_warehouse: warehouseRef(BHIWANDI.id),
    to_warehouse: warehouseRef(PUNE.id),
    line_count: lines.length,
    approval_route: route,
    expected_on: null,
    requested_at: route === null ? null : created,
    dispatched_at: null,
    completed_at: null,
    created_at: created,
    note: "",
    approval_request_id: null,
    approver_name: null,
    requested_by: route === null ? null : "Meera Nair",
    approved_by: null,
    approved_at: null,
    decision_comment: "",
    dispatched_by: null,
    cancelled_at: null,
    cancel_reason: "",
    awaiting_approval: false,
    lines,
    transitions: [{ from_status: null, to_status: "DRAFT", actor: FIXTURE_ACTOR, note: "", created_at: created }],
    replayed: false,
    ...extra,
  };
}

export const TRANSFERS: TransferOut[] = [
  seedTransfer(3, "IN_TRANSIT", "AUTO", [makeLine(1, 7, "40", { dispatched: "40", received: "10" })], {
    dispatched_at: "2026-10-04T10:00:00Z",
    dispatched_by: FIXTURE_ACTOR,
  }),
  seedTransfer(2, "REQUESTED", "DIRECT", [makeLine(1, 8, "120")]),
  seedTransfer(1, "DRAFT", null, [makeLine(1, 13, "250")]),
];

let transferSeq = TRANSFERS.length;

function transferOrRefuse(id: string): TransferOut {
  const found = TRANSFERS.find((t) => t.id === id);
  if (!found) throw problem(404, "That transfer is not part of this organization");
  return found;
}

function step(t: TransferOut, to: TransferStatus, note = ""): void {
  t.transitions.push({ from_status: t.status, to_status: to, actor: FIXTURE_ACTOR, note, created_at: stamp() });
  t.status = to;
}

function linesFrom(input: TransferIn["lines"], start = 1): TransferLineOut[] {
  return input.map((ln, index) => {
    const inv = item(ln.inventory_item_id);
    const from = locationOf(ln.from_location_id);
    return {
      id: `trl-${start + index}-${inv.id}-${Math.random().toString(36).slice(2, 7)}`,
      line_no: start + index,
      inventory_item_id: inv.id,
      sku: inv.sku,
      item_name: inv.name,
      uom: inv.uom,
      from_location_id: from.id,
      from_location_code: from.code,
      to_location_id: null,
      in_transit_location_id: null,
      lot_id: ln.lot_id ?? null,
      roll_id: ln.roll_id ?? null,
      quantity: ln.quantity,
      dispatched: "0",
      received: "0",
      shortage: "0",
      in_transit: "0",
    };
  });
}

export function fixtureCreateTransfer(input: TransferIn): TransferOut {
  if (input.from_warehouse_id === input.to_warehouse_id) {
    throw fieldProblem("to_warehouse_id", "same_warehouse", "The destination must differ from the source warehouse");
  }
  const created = stamp();
  transferSeq += 1;
  const transfer = seedTransfer(transferSeq, "DRAFT", null, linesFrom(input.lines), {
    id: `trf-${transferSeq}`,
    number: number("TRF", transferSeq),
    from_warehouse: warehouseRef(input.from_warehouse_id),
    to_warehouse: warehouseRef(input.to_warehouse_id),
    note: input.note ?? "",
    expected_on: input.expected_on ?? null,
    created_at: created,
    requested_at: null,
    transitions: [{ from_status: null, to_status: "DRAFT", actor: FIXTURE_ACTOR, note: "", created_at: created }],
  });
  TRANSFERS.unshift(transfer);
  return clone(transfer);
}

export interface FixtureTransferFilters {
  status?: TransferStatus | undefined;
  from_warehouse_id?: string | undefined;
  to_warehouse_id?: string | undefined;
  item_id?: string | undefined;
  q?: string | undefined;
  cursor?: string | undefined;
  limit?: number | undefined;
}

export function fixtureListTransfers(filters: FixtureTransferFilters): CursorPage<TransferSummaryOut> {
  const q = filters.q?.trim().toLowerCase() ?? "";
  const rows = TRANSFERS.filter(
    (t) =>
      (filters.status === undefined || t.status === filters.status) &&
      (filters.from_warehouse_id === undefined || t.from_warehouse.id === filters.from_warehouse_id) &&
      (filters.to_warehouse_id === undefined || t.to_warehouse.id === filters.to_warehouse_id) &&
      (filters.item_id === undefined || t.lines.some((l) => l.inventory_item_id === filters.item_id)) &&
      (q === "" || t.number.toLowerCase().startsWith(q)),
  ).map(summaryOf);
  return paged(rows, filters.cursor, filters.limit);
}

export function fixtureGetTransfer(id: string): TransferOut {
  return clone(transferOrRefuse(id));
}

export function fixturePatchTransfer(id: string, patch: TransferPatch): TransferOut {
  const t = transferOrRefuse(id);
  if (t.status !== "DRAFT") throw problem(409, `A transfer that is ${t.status} can no longer be edited`);
  if (patch.to_warehouse_id !== undefined) {
    if (patch.to_warehouse_id === t.from_warehouse.id) {
      throw problem(422, "The destination must differ from the source warehouse");
    }
    t.to_warehouse = warehouseRef(patch.to_warehouse_id);
  }
  if (patch.lines !== undefined) t.lines = linesFrom(patch.lines);
  if (patch.note !== undefined) t.note = patch.note;
  if (patch.expected_on !== undefined) t.expected_on = patch.expected_on;
  t.line_count = t.lines.length;
  return clone(t);
}

export function fixtureRequestTransfer(id: string): TransferOut {
  const t = transferOrRefuse(id);
  if (t.status !== "DRAFT") throw problem(409, `A transfer that is ${t.status} cannot become REQUESTED`);
  const threshold = settings.transfer_approval_threshold;
  const over =
    threshold !== null && t.lines.some((l) => qtyToMilli(l.quantity) >= qtyToMilli(threshold));
  const route: ApprovalRoute = threshold === null ? "DIRECT" : over ? "SERVICE" : "AUTO";
  t.approval_route = route;
  t.requested_at = stamp();
  t.requested_by = FIXTURE_ACTOR;
  step(t, "REQUESTED");
  if (route === "AUTO") {
    step(t, "APPROVED", "Under the approval threshold");
  } else if (route === "SERVICE") {
    t.awaiting_approval = true;
    t.approver_name = "Rohan Malhotra";
  }
  return clone(t);
}

function directDecisionCheck(t: TransferOut, verb: "approve" | "reject"): void {
  if (t.status !== "REQUESTED") throw problem(409, `A transfer that is ${t.status} cannot be ${verb}d`);
  if (t.approval_route !== "DIRECT") {
    throw problem(
      409,
      t.approval_route === "SERVICE"
        ? "This transfer is decided through the approval service, not here"
        : `This transfer needs no direct ${verb}`,
    );
  }
  if (t.requested_by === FIXTURE_ACTOR) {
    throw problem(409, "The person who requested a transfer cannot approve or reject it");
  }
}

export function fixtureApproveTransfer(id: string, input: TransferDecisionIn): TransferOut {
  const t = transferOrRefuse(id);
  directDecisionCheck(t, "approve");
  t.approved_by = FIXTURE_ACTOR;
  t.approved_at = stamp();
  t.decision_comment = input.comment ?? "";
  step(t, "APPROVED", input.comment ?? "");
  return clone(t);
}

export function fixtureRejectTransfer(id: string, input: TransferDecisionIn): TransferOut {
  const t = transferOrRefuse(id);
  directDecisionCheck(t, "reject");
  if ((input.comment ?? "").trim() === "") throw fieldProblem("comment", "required", "A rejection must say why");
  t.decision_comment = input.comment ?? "";
  step(t, "REJECTED", input.comment ?? "");
  return clone(t);
}

export function fixtureCancelTransfer(id: string, input: TransferCancelIn): TransferOut {
  const t = transferOrRefuse(id);
  if (t.status === "IN_TRANSIT") {
    throw problem(409, "The stock is in transit; receive it, or declare what is short");
  }
  if (t.status !== "DRAFT" && t.status !== "REQUESTED" && t.status !== "APPROVED") {
    throw problem(409, `A transfer that is ${t.status} cannot become CANCELLED`);
  }
  t.cancel_reason = input.reason;
  t.cancelled_at = stamp();
  t.awaiting_approval = false;
  step(t, "CANCELLED", input.reason);
  return clone(t);
}

export function fixtureDispatchTransfer(id: string, input: TransferDispatchIn): TransferOut {
  const t = transferOrRefuse(id);
  if (t.status !== "APPROVED") throw problem(409, `A transfer that is ${t.status} cannot be dispatched`);
  const bin = defaultBin(t.to_warehouse.id);
  for (const line of t.lines) {
    const available = qtyToMilli(item(line.inventory_item_id).available_qty);
    if (qtyToMilli(line.quantity) > available) {
      throw shortfall(item(line.inventory_item_id), line.quantity, line.from_location_code);
    }
  }
  for (const line of t.lines) {
    line.dispatched = line.quantity;
    line.in_transit = line.quantity;
    line.in_transit_location_id = bin?.id ?? null;
  }
  t.dispatched_at = stamp();
  t.dispatched_by = FIXTURE_ACTOR;
  if (input.expected_on !== undefined) t.expected_on = input.expected_on;
  step(t, "IN_TRANSIT", input.note ?? "");
  return clone(t);
}

export function fixtureReceiveTransfer(id: string, input: TransferReceiveIn): TransferOut {
  const t = transferOrRefuse(id);
  if (t.status !== "IN_TRANSIT") throw problem(409, `A transfer that is ${t.status} cannot be received`);
  const seen = new Set<string>();
  input.lines.forEach((rl, index) => {
    const line = t.lines.find((l) => l.id === rl.line_id);
    if (!line) throw problem(404, `Line ${rl.line_id} is not part of this transfer`);
    if (seen.has(rl.line_id)) throw problem(422, "Each transfer line may appear once per receipt");
    seen.add(rl.line_id);
    const received = qtyToMilli(rl.received_quantity);
    const short = qtyToMilli(rl.shortage_quantity);
    if (received === 0 && short === 0) {
      throw fieldProblem(`lines[${index}]`, "empty", `${line.sku}: nothing received and nothing short`);
    }
    const remaining = qtyToMilli(line.in_transit);
    if (received + short > remaining) {
      throw problem(
        409,
        `${line.sku}: only ${line.in_transit} is still in transit; cannot account for ${milliToQty(received + short)}`,
      );
    }
  });
  for (const rl of input.lines) {
    const line = t.lines.find((l) => l.id === rl.line_id);
    if (!line) continue;
    line.received = milliToQty(qtyToMilli(line.received) + qtyToMilli(rl.received_quantity));
    line.shortage = milliToQty(qtyToMilli(line.shortage) + qtyToMilli(rl.shortage_quantity));
    line.in_transit = milliToQty(
      qtyToMilli(line.dispatched) - qtyToMilli(line.received) - qtyToMilli(line.shortage),
    );
    line.to_location_id = rl.to_location_id ?? defaultBin(t.to_warehouse.id)?.id ?? line.to_location_id;
  }
  if (t.lines.every((l) => qtyToMilli(l.in_transit) === 0)) {
    t.completed_at = stamp();
    step(t, "RECEIVED", input.note ?? "");
  }
  return clone(t);
}

export function fixtureInTransit(filters: { warehouse_id?: string | undefined; item_id?: string | undefined }): InTransitRowOut[] {
  const rows = new Map<string, InTransitRowOut>();
  for (const t of TRANSFERS) {
    if (t.status !== "IN_TRANSIT") continue;
    if (filters.warehouse_id !== undefined && t.to_warehouse.id !== filters.warehouse_id) continue;
    for (const line of t.lines) {
      if (filters.item_id !== undefined && line.inventory_item_id !== filters.item_id) continue;
      if (qtyToMilli(line.in_transit) === 0) continue;
      const key = `${line.inventory_item_id}:${t.to_warehouse.id}`;
      const existing = rows.get(key);
      if (existing) {
        existing.in_transit = milliToQty(qtyToMilli(existing.in_transit) + qtyToMilli(line.in_transit));
        continue;
      }
      const inv = item(line.inventory_item_id);
      rows.set(key, {
        inventory_item_id: inv.id,
        sku: inv.sku,
        item_name: inv.name,
        uom: inv.uom,
        warehouse_id: t.to_warehouse.id,
        warehouse_code: t.to_warehouse.code,
        in_transit: line.in_transit,
        physical: inv.warehouse === t.to_warehouse.name ? inv.on_hand_qty : "0",
      });
    }
  }
  return [...rows.values()];
}

/* -------------------------------- reservations -------------------------------- */

function shortfall(inv: InventoryItemOut, requested: Qty, where: string, blockers: ReservationDetailOut[] = []): ApiError {
  const available = inv.available_qty;
  const detail =
    `Cannot take ${requested} ${inv.uom} of ${inv.sku} from ${where}: ${inv.on_hand_qty} ${inv.uom} on hand, ` +
    `${inv.reserved_qty} ${inv.uom} reserved, so ${available} ${inv.uom} available`;
  return problem(409, detail, {
    type: "https://errors.opsgrids.dev/insufficient-stock",
    title: "Not enough stock",
    errors: [
      {
        sku: inv.sku,
        location: where,
        status: "AVAILABLE",
        uom: inv.uom,
        requested,
        on_hand: inv.on_hand_qty,
        reserved: inv.reserved_qty,
        available,
        reservations: blockers.map((r) => ({
          reservation_id: r.id,
          reference_type: r.reference_type,
          reference_id: r.reference_id,
          quantity: r.remaining,
        })),
      },
    ] as unknown as ProblemJson["errors"],
  });
}

export const RESERVATIONS: ReservationDetailOut[] = [];

function seedReservation(
  n: number,
  itemIndex: number,
  quantity: Qty,
  extra: Partial<ReservationDetailOut> = {},
): ReservationDetailOut {
  const inv = INVENTORY[itemIndex];
  const bin = BINS[0];
  if (!inv || !bin) throw new Error("fixture inventory is missing");
  return {
    id: `res-${n}`,
    inventory_item_id: inv.id,
    sku: inv.sku,
    item_name: inv.name,
    uom: inv.uom,
    location_id: bin.id,
    location_code: bin.code,
    lot_id: null,
    roll_id: null,
    quantity,
    consumed: "0",
    released: "0",
    transferred: "0",
    remaining: quantity,
    status: "active",
    reference_type: "manual",
    reference_id: null,
    expires_at: null,
    expired_at: null,
    note: "",
    created_at: `2026-10-0${n}T09:00:00Z`,
    closed_at: null,
    replayed: false,
    ...extra,
  };
}

RESERVATIONS.push(
  seedReservation(3, 7, "20", { reference_type: "order", reference_id: "5b6f3c1e-4a9d-4e0a-9d51-2f7a1c0b8e11" }),
  seedReservation(2, 8, "100", { expires_at: "2026-10-12T18:00:00Z" }),
  seedReservation(1, 13, "30", {
    status: "released",
    released: "30",
    remaining: "0",
    expired_at: "2026-10-03T06:00:00Z",
    expires_at: "2026-10-03T06:00:00Z",
    closed_at: "2026-10-03T06:00:00Z",
  }),
);

let reservationSeq = RESERVATIONS.length;

export interface FixtureReservationFilters {
  status?: ReservationStatus | undefined;
  reference_type?: string | undefined;
  item_id?: string | undefined;
  location_id?: string | undefined;
  cursor?: string | undefined;
  limit?: number | undefined;
}

export function fixtureListReservations(filters: FixtureReservationFilters): CursorPage<ReservationDetailOut> {
  const rows = RESERVATIONS.filter(
    (r) =>
      (filters.status === undefined || r.status === filters.status) &&
      (filters.reference_type === undefined || r.reference_type === filters.reference_type) &&
      (filters.item_id === undefined || r.inventory_item_id === filters.item_id) &&
      (filters.location_id === undefined || r.location_id === filters.location_id),
  );
  return paged(clone(rows), filters.cursor, filters.limit);
}

function reservationOrRefuse(id: string): ReservationDetailOut {
  const found = RESERVATIONS.find((r) => r.id === id);
  if (!found) throw problem(404, "That reservation is not part of this organization");
  return found;
}

export function fixtureGetReservation(id: string): ReservationDetailOut {
  return clone(reservationOrRefuse(id));
}

export function fixtureCreateReservation(input: ReservationIn): ReservationDetailOut {
  const inv = item(input.inventory_item_id);
  const where = locationOf(input.location_id);
  const quantity = qtyToMilli(input.quantity);
  if (quantity <= 0) throw fieldProblem("quantity", "invalid_quantity", "a reservation is a positive quantity");
  if ((input.reference_type ?? "manual") !== "manual" && !input.reference_id) {
    throw fieldProblem("reference_id", "required", "an order or production-order reservation names that document");
  }
  if (input.expires_at && Date.parse(input.expires_at) <= Date.now()) {
    throw fieldProblem("expires_at", "in_the_past", "The expiry has to be in the future.");
  }
  if (quantity > qtyToMilli(inv.available_qty)) {
    const blockers = RESERVATIONS.filter((r) => r.inventory_item_id === inv.id && r.status === "active");
    throw shortfall(inv, input.quantity, where.code, blockers);
  }
  reservationSeq += 1;
  const created = seedReservation(reservationSeq, INVENTORY.indexOf(inv), milliToQty(quantity), {
    id: `res-${reservationSeq}`,
    location_id: where.id,
    location_code: where.code,
    lot_id: input.lot_id ?? null,
    roll_id: input.roll_id ?? null,
    reference_type: input.reference_type ?? "manual",
    reference_id: input.reference_id ?? null,
    expires_at: input.expires_at ?? null,
    note: input.note ?? "",
    created_at: stamp(),
  });
  RESERVATIONS.unshift(created);
  const index = INVENTORY.indexOf(inv);
  INVENTORY[index] = {
    ...inv,
    reserved_qty: milliToQty(qtyToMilli(inv.reserved_qty) + quantity),
    available_qty: milliToQty(qtyToMilli(inv.available_qty) - quantity),
  };
  return clone(created);
}

export function fixtureReleaseReservation(id: string, input: ReservationReleaseIn): ReservationReleaseOut {
  const r = reservationOrRefuse(id);
  if (r.status !== "active") throw problem(409, `A reservation that is ${r.status} has nothing left to release`);
  const remaining = qtyToMilli(r.remaining);
  const giving = input.quantity === undefined ? remaining : qtyToMilli(input.quantity);
  if (giving <= 0) throw fieldProblem("quantity", "invalid_quantity", "Release a quantity above zero.");
  if (giving > remaining) {
    throw problem(409, `Only ${r.remaining} ${r.uom} is still reserved; cannot release ${milliToQty(giving)}`);
  }
  r.released = milliToQty(qtyToMilli(r.released) + giving);
  r.remaining = milliToQty(remaining - giving);
  if (remaining - giving === 0) {
    r.status = "released";
    r.closed_at = stamp();
  }
  const inv = INVENTORY.find((i) => i.id === r.inventory_item_id);
  if (inv) {
    const index = INVENTORY.indexOf(inv);
    INVENTORY[index] = {
      ...inv,
      reserved_qty: milliToQty(Math.max(qtyToMilli(inv.reserved_qty) - giving, 0)),
      available_qty: milliToQty(qtyToMilli(inv.available_qty) + giving),
    };
  }
  return {
    id: r.id,
    inventory_item_id: r.inventory_item_id,
    quantity: r.quantity,
    consumed: r.consumed,
    released: r.released,
    transferred: r.transferred,
    remaining: r.remaining,
    status: r.status,
    reference_type: r.reference_type,
    reference_id: r.reference_id,
  };
}

/* --------------------------------- adjustments -------------------------------- */

export const ADJUSTMENTS: AdjustmentOut[] = [];

function seedAdjustment(
  n: number,
  itemIndex: number,
  delta: Qty,
  status: AdjustmentStatus,
  extra: Partial<AdjustmentOut> = {},
): AdjustmentOut {
  const inv = INVENTORY[itemIndex];
  const bin = BINS[0];
  if (!inv || !bin) throw new Error("fixture inventory is missing");
  return {
    id: `adj-${n}`,
    number: number("ADJ", n),
    inventory_item_id: inv.id,
    sku: inv.sku,
    item_name: inv.name,
    uom: inv.uom,
    location_id: bin.id,
    location_code: bin.code,
    stock_status: "AVAILABLE",
    lot_id: null,
    roll_id: null,
    quantity_delta: delta,
    reason: "damaged",
    note: "",
    evidence_document_id: null,
    occurred_on: "2026-10-04",
    status,
    approval_required: status === "PENDING_APPROVAL",
    approval_request_id: null,
    approver_name: status === "PENDING_APPROVAL" ? "Rohan Malhotra" : null,
    decided_by_name: null,
    decided_at: null,
    decision_comment: "",
    requested_by: FIXTURE_ACTOR,
    applied_move_id: status === "APPLIED" ? `mv-${n}` : null,
    applied_at: status === "APPLIED" ? `2026-10-04T09:00:00Z` : null,
    failure_reason: "",
    created_at: `2026-10-0${n}T09:00:00Z`,
    replayed: false,
    ...extra,
  };
}

ADJUSTMENTS.push(
  seedAdjustment(2, 8, "-80", "PENDING_APPROVAL", { evidence_document_id: "0b8f3a52-6f1d-4e0c-8a3e-3b7c5d9e1f20" }),
  seedAdjustment(1, 13, "-4", "APPLIED"),
);

let adjustmentSeq = ADJUSTMENTS.length;

export function fixtureListAdjustments(filters: {
  status?: AdjustmentStatus | undefined;
  item_id?: string | undefined;
  cursor?: string | undefined;
  limit?: number | undefined;
}): CursorPage<AdjustmentOut> {
  const rows = ADJUSTMENTS.filter(
    (a) =>
      (filters.status === undefined || a.status === filters.status) &&
      (filters.item_id === undefined || a.inventory_item_id === filters.item_id),
  );
  return paged(clone(rows), filters.cursor, filters.limit);
}

function adjustmentOrRefuse(id: string): AdjustmentOut {
  const found = ADJUSTMENTS.find((a) => a.id === id);
  if (!found) throw problem(404, "That adjustment is not part of this organization");
  return found;
}

export function fixtureGetAdjustment(id: string): AdjustmentOut {
  return clone(adjustmentOrRefuse(id));
}

export function fixtureCreateAdjustment(input: AdjustmentIn): AdjustmentOut {
  const inv = item(input.inventory_item_id);
  const where = locationOf(input.location_id);
  const delta = qtyToMilli(input.quantity_delta);
  if (delta === 0) throw fieldProblem("quantity_delta", "invalid_quantity", "an adjustment must change the quantity");
  const threshold = settings.adjustment_approval_threshold;
  const needsApproval = threshold !== null && Math.abs(delta) >= qtyToMilli(threshold);
  if (needsApproval && !input.evidence_document_id) {
    throw fieldProblem(
      "evidence_document_id",
      "required",
      "An adjustment this size needs approval, and approval needs evidence: attach a document.",
    );
  }
  if (!needsApproval && delta < 0 && -delta > qtyToMilli(inv.available_qty)) {
    throw shortfall(inv, milliToQty(-delta), where.code);
  }
  adjustmentSeq += 1;
  const created = seedAdjustment(adjustmentSeq, INVENTORY.indexOf(inv), milliToQty(delta), needsApproval ? "PENDING_APPROVAL" : "APPLIED", {
    id: `adj-${adjustmentSeq}`,
    location_id: where.id,
    location_code: where.code,
    reason: input.reason,
    note: input.note ?? "",
    evidence_document_id: input.evidence_document_id ?? null,
    occurred_on: input.occurred_on ?? stamp().slice(0, 10),
    approval_required: needsApproval,
    created_at: stamp(),
    applied_at: needsApproval ? null : stamp(),
  });
  if (!needsApproval) {
    const index = INVENTORY.indexOf(inv);
    INVENTORY[index] = {
      ...inv,
      on_hand_qty: milliToQty(qtyToMilli(inv.on_hand_qty) + delta),
      available_qty: milliToQty(qtyToMilli(inv.available_qty) + delta),
    };
  }
  ADJUSTMENTS.unshift(created);
  return clone(created);
}

export function fixtureCancelAdjustment(id: string, input: AdjustmentCancelIn): AdjustmentOut {
  const a = adjustmentOrRefuse(id);
  if (a.status !== "PENDING_APPROVAL") {
    throw problem(409, `An adjustment that is ${a.status} cannot become CANCELLED`);
  }
  a.status = "CANCELLED";
  a.decision_comment = input.reason ?? "";
  return clone(a);
}
