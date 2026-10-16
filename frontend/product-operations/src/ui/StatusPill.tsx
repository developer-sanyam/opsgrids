import type {
  AdjustmentStatus,
  ApprovalRoute,
  ReservationStatus,
  TransferStatus,
} from "@/api/movementTypes";
import type {
  BomVersionStatus,
  ChannelStatus,
  ChannelSyncStatus,
  CollectionState,
  CountLineReviewStatus,
  CountSessionStatus,
  EwayStatus,
  JobWorkStatus,
  InspectionStatus,
  MrpRunStatus,
  OfferState,
  OrderStatus,
  PickTaskStatus,
  PoStatus,
  ProductionOrderStatus,
  ReceiptOutcome,
  RequisitionStatus,
  ShipmentStatus,
  StockStatus,
  SupplierStatus,
} from "@/api/types";
import { AlertTriangleIcon, StatusBadge } from "@opsgrids/ui";
import type { StatusTone, StatusWord } from "@opsgrids/ui";

/*
 * Thin wrappers over the shared `StatusBadge`. "draft", "approved", "received", "closed" and
 * "cancelled" (PO), and "active" and "pending" (supplier) are registered words; "on_hold" is also
 * registered (warning). No unregistered words here.
 */

/* ------------------------------ PO status ------------------------------ */

const PO_LABEL: Record<PoStatus, string> = {
  draft: "Draft",
  approved: "Approved",
  partially_received: "Partially received",
  received: "Received",
  closed: "Closed",
  cancelled: "Cancelled",
};

/** Purchase-order lifecycle. "Partially received" is not a registered word: part of the order has
 *  landed and the rest is still expected, which is the registry's `in_progress` (info). */
export function PoStatusPill({ status }: { status: PoStatus }) {
  if (status === "partially_received") {
    return <StatusBadge status="in_progress" label={PO_LABEL[status]} />;
  }
  return <StatusBadge status={status} label={PO_LABEL[status]} />;
}

/* ------------------------- purchase requisition status ------------------------ */
/** Every requisition status is a registered word, so each carries the registry's tone. */

const REQUISITION_LABEL: Record<RequisitionStatus, string> = {
  DRAFT: "Draft",
  SUBMITTED: "Submitted",
  APPROVED: "Approved",
  REJECTED: "Rejected",
  CONVERTED: "Converted",
  CANCELLED: "Cancelled",
};

const REQUISITION_WORD: Record<RequisitionStatus, StatusWord> = {
  DRAFT: "draft",
  SUBMITTED: "submitted",
  APPROVED: "approved",
  REJECTED: "rejected",
  CONVERTED: "converted",
  CANCELLED: "cancelled",
};

export function RequisitionStatusPill({ status }: { status: RequisitionStatus }) {
  return <StatusBadge status={REQUISITION_WORD[status]} label={REQUISITION_LABEL[status]} />;
}

/* --------------------------- goods receipt outcome ---------------------------- */
/** How a receipt line left its purchase order line. None is a registered word: an exact receipt
 *  is a good outcome, partial is in motion, and short or over each want a second look. */

const OUTCOME_LABEL: Record<ReceiptOutcome, string> = {
  PARTIAL: "Partial",
  EXACT: "Exact",
  SHORT: "Short",
  OVER: "Over",
};

const OUTCOME_TONE: Record<ReceiptOutcome, StatusTone> = {
  PARTIAL: "info",
  EXACT: "success",
  SHORT: "warning",
  OVER: "warning",
};

export function ReceiptOutcomePill({ outcome }: { outcome: ReceiptOutcome }) {
  return <StatusBadge tone={OUTCOME_TONE[outcome]} label={OUTCOME_LABEL[outcome]} />;
}

/* ------------------------------- stock status -------------------------------- */

const STOCK_STATUS_LABEL: Record<StockStatus, string> = {
  AVAILABLE: "Available",
  QUALITY_HOLD: "Quality hold",
  QUARANTINE: "Quarantine",
  DAMAGED: "Damaged",
  REJECTED: "Rejected",
  WIP: "Work in progress",
  IN_TRANSIT: "In transit",
  AT_JOB_WORKER: "At job worker",
  BLOCKED: "Blocked",
};

const STOCK_STATUS_TONE: Record<StockStatus, StatusTone> = {
  AVAILABLE: "success",
  QUALITY_HOLD: "warning",
  QUARANTINE: "warning",
  DAMAGED: "danger",
  REJECTED: "danger",
  WIP: "info",
  IN_TRANSIT: "info",
  AT_JOB_WORKER: "info",
  BLOCKED: "danger",
};

/** The condition a bucket of stock is in. Always the word, never colour alone. */
export function StockStatusPill({ status }: { status: StockStatus }) {
  return <StatusBadge tone={STOCK_STATUS_TONE[status]} label={STOCK_STATUS_LABEL[status]} />;
}

/* --------------------------- supplier status --------------------------- */

const SUPPLIER_LABEL: Record<SupplierStatus, string> = {
  active: "Active",
  on_hold: "On hold",
  pending: "Pending",
};

export function SupplierStatusPill({ status }: { status: SupplierStatus }) {
  return <StatusBadge status={status} label={SUPPLIER_LABEL[status]} />;
}

/* ------------------------------- order status --------------------------- */
/**
 * ADR-0025 §1's order state machine. Registered words carry the registry's tone: `draft`
 * (neutral), `accepted` (success), `on_hold` (warning), `delivered` (success), `completed`
 * (success), `cancelled` (danger), `rejected` (danger). `placed`, `in_fulfilment`, `ready` and
 * `handed_over` are not registered words — each takes an explicit `info` tone (in motion, outcome
 * not yet known), the same reasoning the registry's own `sent`/`submitted` entries document.
 */

const ORDER_LABEL: Record<OrderStatus, string> = {
  DRAFT: "Draft",
  PLACED: "Placed",
  ACCEPTED: "Accepted",
  IN_FULFILMENT: "In fulfilment",
  ON_HOLD: "On hold",
  READY: "Ready",
  HANDED_OVER: "Handed over",
  DELIVERED: "Delivered",
  COMPLETED: "Completed",
  CANCELLED: "Cancelled",
  REJECTED: "Rejected",
};

const ORDER_REGISTERED: Partial<Record<OrderStatus, StatusWord>> = {
  DRAFT: "draft",
  ACCEPTED: "accepted",
  ON_HOLD: "on_hold",
  DELIVERED: "delivered",
  COMPLETED: "completed",
  CANCELLED: "cancelled",
  REJECTED: "rejected",
};

/** Order lifecycle (Fulfilment F2/ADR-0025). */
export function OrderStatusPill({ status }: { status: OrderStatus }) {
  const word = ORDER_REGISTERED[status];
  if (word) return <StatusBadge status={word} label={ORDER_LABEL[status]} />;
  return <StatusBadge tone="info" label={ORDER_LABEL[status]} />;
}

/* ---------------------------- pick task status --------------------------- */
/**
 * ADR-0025 §2's pick-task state machine. Registered words carry the registry's tone: `accepted`
 * (success), `in_progress` (info), `closed` (neutral), `cancelled` (danger). `created`, `offered`,
 * `escalated`, `exception` and `picked` are not registered words — each takes an explicit tone.
 */

const TASK_LABEL: Record<PickTaskStatus, string> = {
  CREATED: "Created",
  OFFERED: "Offered",
  ESCALATED: "Escalated",
  ACCEPTED: "Accepted",
  IN_PROGRESS: "In progress",
  EXCEPTION: "Exception",
  PICKED: "Picked",
  CLOSED: "Closed",
  CANCELLED: "Cancelled",
};

const TASK_REGISTERED: Partial<Record<PickTaskStatus, StatusWord>> = {
  ACCEPTED: "accepted",
  IN_PROGRESS: "in_progress",
  CLOSED: "closed",
  CANCELLED: "cancelled",
};

const TASK_EXPLICIT_TONE: Partial<Record<PickTaskStatus, StatusTone>> = {
  CREATED: "neutral",
  OFFERED: "info",
  ESCALATED: "warning",
  EXCEPTION: "danger",
  PICKED: "success",
};

/** Pick-task lifecycle (Fulfilment F3/ADR-0025 §2). */
export function PickTaskStatusPill({ status }: { status: PickTaskStatus }) {
  const word = TASK_REGISTERED[status];
  if (word) return <StatusBadge status={word} label={TASK_LABEL[status]} />;
  return <StatusBadge tone={TASK_EXPLICIT_TONE[status] ?? "neutral"} label={TASK_LABEL[status]} />;
}

/* -------------------------- assignment offer state ------------------------- */
/** ADR-0025 §3's offer state machine. */

const OFFER_LABEL: Record<OfferState, string> = {
  OFFERED: "Offered",
  ACCEPTED: "Accepted",
  DECLINED: "Declined",
  EXPIRED: "Expired",
  WITHDRAWN: "Withdrawn",
  RELEASED: "Released",
};

const OFFER_REGISTERED: Partial<Record<OfferState, StatusWord>> = {
  ACCEPTED: "accepted",
  DECLINED: "declined",
  EXPIRED: "expired",
};

const OFFER_EXPLICIT_TONE: Partial<Record<OfferState, StatusTone>> = {
  OFFERED: "info",
  WITHDRAWN: "danger",
  RELEASED: "warning",
};

export function AssignmentOfferStatusPill({ state }: { state: OfferState }) {
  const word = OFFER_REGISTERED[state];
  if (word) return <StatusBadge status={word} label={OFFER_LABEL[state]} />;
  return <StatusBadge tone={OFFER_EXPLICIT_TONE[state] ?? "neutral"} label={OFFER_LABEL[state]} />;
}

/** Auto vs manual, read off `AssignmentOfferOut.score_breakdown.kind` (auto-assignment; not a
 *  registered status word — the offer's own history, not its lifecycle). `undefined`/anything
 *  else falls back to "Manual", since every offer before this build only ever had `"kind":
 *  "manual"` or no `kind` at all. */
export function AssignmentKindPill({ kind }: { kind: unknown }) {
  return kind === "auto" ? (
    <StatusBadge tone="info" label="Auto" />
  ) : (
    <StatusBadge tone="neutral" label="Manual" />
  );
}

/* ------------------------------ shipment status -------------------------- */
/**
 * The carrier lifecycle (`app.domain.SHIPMENT_TRANSITIONS`, Fulfilment F5). Registered words
 * carry the registry's tone: `delivered` (success), `failed` (danger). `pickup_handover`,
 * `in_transit` and `returned` are not registered words — each takes an explicit tone.
 */

const SHIPMENT_LABEL: Record<ShipmentStatus, string> = {
  BOOKING: "Booking",
  BOOKING_FAILED: "Booking failed",
  PICKUP_HANDOVER: "Pickup handover",
  IN_TRANSIT: "In transit",
  DELIVERED: "Delivered",
  FAILED: "Failed",
  RETURNED: "Returned",
};

const SHIPMENT_REGISTERED: Partial<Record<ShipmentStatus, StatusWord>> = {
  DELIVERED: "delivered",
  FAILED: "failed",
};

const SHIPMENT_EXPLICIT_TONE: Partial<Record<ShipmentStatus, StatusTone>> = {
  BOOKING: "info",
  BOOKING_FAILED: "danger",
  PICKUP_HANDOVER: "info",
  IN_TRANSIT: "info",
  RETURNED: "danger",
};

export function ShipmentStatusPill({ status }: { status: ShipmentStatus }) {
  const word = SHIPMENT_REGISTERED[status];
  if (word) return <StatusBadge status={word} label={SHIPMENT_LABEL[status]} />;
  return <StatusBadge tone={SHIPMENT_EXPLICIT_TONE[status] ?? "neutral"} label={SHIPMENT_LABEL[status]} />;
}

/* ------------------------------ online payment (ADR-0024 A4) ---------------------------- */

const COLLECTION_LABEL: Record<CollectionState, string> = {
  created: "Awaiting payment",
  authorized: "Authorized",
  captured: "Paid",
  failed: "Attempt failed",
  cancelled: "Cancelled",
  expired: "Expired",
};

/** None of these is a registered status word in the sense this screen means it ("failed" here is
 *  one attempt that the customer may still retry), so each takes an explicit tone. */
const COLLECTION_TONE: Record<CollectionState, StatusTone> = {
  created: "info",
  authorized: "warning",
  captured: "success",
  failed: "danger",
  cancelled: "neutral",
  expired: "neutral",
};

export function CollectionStatusPill({ state }: { state: CollectionState }) {
  return <StatusBadge tone={COLLECTION_TONE[state]} label={COLLECTION_LABEL[state]} />;
}

/* ------------------------------ channel status --------------------------- */
/** A channel connection's own health (Fulfilment F11) — `connected` (success), `disconnected`
 *  (neutral, a deliberate state, not a failure), `error` (danger — the last sync hit an auth
 *  failure and needs a fresh credential, `app.models.Channel`'s own docstring). */

const CHANNEL_LABEL: Record<ChannelStatus, string> = {
  connected: "Connected",
  disconnected: "Disconnected",
  error: "Needs reconnect",
};

const CHANNEL_TONE: Record<ChannelStatus, StatusTone> = {
  connected: "success",
  disconnected: "neutral",
  error: "danger",
};

export function ChannelStatusPill({ status }: { status: ChannelStatus }) {
  return <StatusBadge tone={CHANNEL_TONE[status]} label={CHANNEL_LABEL[status]} />;
}

/* ---------------------------- channel sync status ------------------------- */

const CHANNEL_SYNC_LABEL: Record<ChannelSyncStatus, string> = {
  success: "Success",
  failed: "Failed",
};

const CHANNEL_SYNC_TONE: Record<ChannelSyncStatus, StatusTone> = {
  success: "success",
  failed: "danger",
};

export function ChannelSyncStatusPill({ status }: { status: ChannelSyncStatus }) {
  return <StatusBadge tone={CHANNEL_SYNC_TONE[status]} label={CHANNEL_SYNC_LABEL[status]} />;
}

/* ------------------------------ stock level ---------------------------- */

export type StockLevel = "ok" | "low" | "out";

const STOCK_TONE: Record<StockLevel, StatusTone> = {
  ok: "neutral",
  low: "warning",
  out: "danger",
};

const STOCK_LABEL: Record<StockLevel, string> = {
  ok: "In stock",
  low: "Low stock",
  out: "Out of stock",
};

/**
 * Stock health, always **labelled** — never colour alone. "Low" and "Out" carry a warning icon as
 * well as the badge, so the column reads consistently. Neither "ok"/"low"/"out" is a registered
 * word, so each takes an explicit tone.
 */
export function StockPill({ level }: { level: StockLevel }) {
  if (level === "ok") return <StatusBadge tone={STOCK_TONE.ok} label={STOCK_LABEL.ok} />;
  return (
    <span className="inline-flex items-center gap-1.5">
      <AlertTriangleIcon className="size-3.5" strokeWidth={2} />
      <StatusBadge tone={STOCK_TONE[level]} label={STOCK_LABEL[level]} />
    </span>
  );
}

/* ---------------------------- count session status ---------------------------- */
/**
 * ADR-0026 §6's `COUNT_SESSION_TRANSITIONS` vocabulary. `REVIEW` is the registered word
 * `in_review` (info) — an exact semantic match, not a coincidence of spelling. `ACTIVE` takes the
 * registered `in_progress` (info) rather than `active` (success): a count in flight is not yet a
 * good outcome, only a fact in motion.
 */

const COUNT_SESSION_LABEL: Record<CountSessionStatus, string> = {
  ACTIVE: "Active",
  REVIEW: "In review",
  CLOSED: "Closed",
  CANCELLED: "Cancelled",
};

const COUNT_SESSION_WORD: Record<CountSessionStatus, StatusWord> = {
  ACTIVE: "in_progress",
  REVIEW: "in_review",
  CLOSED: "closed",
  CANCELLED: "cancelled",
};

export function CountSessionStatusPill({ status }: { status: CountSessionStatus }) {
  return <StatusBadge status={COUNT_SESSION_WORD[status]} label={COUNT_SESSION_LABEL[status]} />;
}

/* -------------------------- count line review status -------------------------- */
/** `NONE` (nothing to review) is not a registered word and is shown quietly, neutral. */

const COUNT_LINE_LABEL: Record<CountLineReviewStatus, string> = {
  NONE: "—",
  PENDING: "Pending review",
  APPROVED: "Approved",
  REJECTED: "Rejected",
};

const COUNT_LINE_REGISTERED: Partial<Record<CountLineReviewStatus, StatusWord>> = {
  PENDING: "pending",
  APPROVED: "approved",
  REJECTED: "rejected",
};

export function CountLineReviewStatusPill({ status }: { status: CountLineReviewStatus }) {
  const word = COUNT_LINE_REGISTERED[status];
  if (word) return <StatusBadge status={word} label={COUNT_LINE_LABEL[status]} />;
  return <StatusBadge tone="neutral" label={COUNT_LINE_LABEL[status]} />;
}

/* ---------------------------- transfer status (Phase 3) ---------------------------- */
/**
 * The transfer state machine (`TRANSFER_TRANSITIONS`). Registered words keep the registry's tone:
 * `draft`, `approved`, `received`, `cancelled`, `rejected`. `REQUESTED` and `IN_TRANSIT` are not
 * registered words and take explicit tones — waiting on a person (warning), and in motion (info).
 */

const TRANSFER_LABEL: Record<TransferStatus, string> = {
  DRAFT: "Draft",
  REQUESTED: "Requested",
  APPROVED: "Approved",
  IN_TRANSIT: "In transit",
  RECEIVED: "Received",
  CANCELLED: "Cancelled",
  REJECTED: "Rejected",
};

const TRANSFER_REGISTERED: Partial<Record<TransferStatus, StatusWord>> = {
  DRAFT: "draft",
  APPROVED: "approved",
  RECEIVED: "received",
  CANCELLED: "cancelled",
  REJECTED: "rejected",
};

export function TransferStatusPill({ status }: { status: TransferStatus }) {
  const word = TRANSFER_REGISTERED[status];
  if (word) return <StatusBadge status={word} label={TRANSFER_LABEL[status]} />;
  return <StatusBadge tone={status === "REQUESTED" ? "warning" : "info"} label={TRANSFER_LABEL[status]} />;
}

/** How a requested transfer is signed off, in words — never the enum, never colour alone. */
const APPROVAL_ROUTE_LABEL: Record<ApprovalRoute, string> = {
  AUTO: "Approved automatically",
  DIRECT: "Direct approval",
  SERVICE: "Approval service",
};

export function ApprovalRouteChip({ route }: { route: ApprovalRoute | null }) {
  if (route === null) return <span className="text-faint">Not requested yet</span>;
  return <StatusBadge tone="neutral" label={APPROVAL_ROUTE_LABEL[route]} />;
}

/* --------------------------- adjustment status (Phase 3) --------------------------- */

const ADJUSTMENT_LABEL: Record<AdjustmentStatus, string> = {
  PENDING_APPROVAL: "Waiting for approval",
  APPLIED: "Applied",
  REJECTED: "Rejected",
  CANCELLED: "Cancelled",
  FAILED: "Failed",
};

const ADJUSTMENT_REGISTERED: Partial<Record<AdjustmentStatus, StatusWord>> = {
  PENDING_APPROVAL: "awaiting_approval",
  REJECTED: "rejected",
  CANCELLED: "cancelled",
  FAILED: "failed",
};

export function AdjustmentStatusPill({ status }: { status: AdjustmentStatus }) {
  const word = ADJUSTMENT_REGISTERED[status];
  if (word) return <StatusBadge status={word} label={ADJUSTMENT_LABEL[status]} />;
  return <StatusBadge tone="success" label={ADJUSTMENT_LABEL[status]} />;
}

/* -------------------------- reservation status (Phase 3) --------------------------- */

const RESERVATION_LABEL: Record<ReservationStatus, string> = {
  active: "Active",
  released: "Released",
  consumed: "Used",
  transferred: "Moved",
};

const RESERVATION_TONE: Record<ReservationStatus, StatusTone> = {
  active: "info",
  released: "neutral",
  consumed: "success",
  transferred: "neutral",
};

/** A reservation that lapsed is shown as "Expired" — the word the expiry sweep earned it — rather
 *  than the "Released" its status says, which would hide why the stock came back. */
export function ReservationStatusPill({ status, expired }: { status: ReservationStatus; expired: boolean }) {
  if (expired) return <StatusBadge status="expired" label="Expired" />;
  return <StatusBadge tone={RESERVATION_TONE[status]} label={RESERVATION_LABEL[status]} />;
}

/* ------------------------------ inspection status ------------------------------ */
/**
 * `app.quality.domain.INSPECTION_TRANSITIONS`' vocabulary. `PENDING`, `HOLD` (`on_hold`) and
 * `FAILED` are registered words with the right tone; `PASSED`, `REWORK` and `PARTIAL_PASS` are not
 * and each takes an explicit one. Always labelled — never colour alone.
 */

const INSPECTION_LABEL: Record<InspectionStatus, string> = {
  PENDING: "Pending",
  PASSED: "Passed",
  FAILED: "Failed",
  HOLD: "On hold",
  REWORK: "Rework",
  PARTIAL_PASS: "Partial pass",
};

const INSPECTION_REGISTERED: Partial<Record<InspectionStatus, StatusWord>> = {
  PENDING: "pending",
  FAILED: "failed",
  HOLD: "on_hold",
};

const INSPECTION_EXPLICIT_TONE: Partial<Record<InspectionStatus, StatusTone>> = {
  PASSED: "success",
  REWORK: "warning",
  PARTIAL_PASS: "info",
};

export function InspectionStatusPill({ status }: { status: InspectionStatus }) {
  const word = INSPECTION_REGISTERED[status];
  if (word) return <StatusBadge status={word} label={INSPECTION_LABEL[status]} />;
  return <StatusBadge tone={INSPECTION_EXPLICIT_TONE[status] ?? "neutral"} label={INSPECTION_LABEL[status]} />;
}

/* ------------------------- manufacturing statuses -------------------------- */
/** A BOM version: DRAFT (neutral), PUBLISHED (success), RETIRED (neutral) — all registered words. */

const BOM_VERSION_WORD: Record<BomVersionStatus, StatusWord> = {
  DRAFT: "draft",
  PUBLISHED: "published",
  RETIRED: "retired",
};

const BOM_VERSION_LABEL: Record<BomVersionStatus, string> = {
  DRAFT: "Draft",
  PUBLISHED: "Published",
  RETIRED: "Retired",
};

export function BomVersionStatusPill({ status }: { status: BomVersionStatus }) {
  return <StatusBadge status={BOM_VERSION_WORD[status]} label={BOM_VERSION_LABEL[status]} />;
}

/** A production order: DRAFT -> APPROVED -> IN_PROGRESS -> COMPLETED, or CANCELLED. */

const PRODUCTION_ORDER_WORD: Record<ProductionOrderStatus, StatusWord> = {
  DRAFT: "draft",
  APPROVED: "approved",
  IN_PROGRESS: "in_progress",
  COMPLETED: "completed",
  CANCELLED: "cancelled",
};

const PRODUCTION_ORDER_LABEL: Record<ProductionOrderStatus, string> = {
  DRAFT: "Draft",
  APPROVED: "Approved",
  IN_PROGRESS: "In progress",
  COMPLETED: "Completed",
  CANCELLED: "Cancelled",
};

export function ProductionOrderStatusPill({ status }: { status: ProductionOrderStatus }) {
  return <StatusBadge status={PRODUCTION_ORDER_WORD[status]} label={PRODUCTION_ORDER_LABEL[status]} />;
}

/* ------------------------------- MRP run status ------------------------------ */
/**
 * An MRP run: `QUEUED` (waiting for the worker) -> `COMPLETED` | `FAILED`. `completed` is a
 * registered word; "queued" and "failed" are not, so each takes an explicit tone.
 */
export function MrpRunStatusPill({ status }: { status: MrpRunStatus }) {
  if (status === "COMPLETED") return <StatusBadge status="completed" label="Completed" />;
  if (status === "FAILED") return <StatusBadge tone="danger" label="Failed" />;
  return <StatusBadge tone="info" label="Queued" />;
}

/* ---------------------------------- job work status ---------------------------------- */
/**
 * `app.jobwork.domain.JOB_WORK_TRANSITIONS`. Registered words carry the registry's tone: `draft`,
 * `approved`, `received`, `closed`, `cancelled`. `ISSUED` (material is out: in motion, outcome not
 * yet known — `info`) and `PARTIALLY_RECEIVED` (some back, some still out — `warning`) are not
 * registered words and take explicit tones. **Overdue is not a status** — it is a flag on the
 * order, shown by `OverduePill` beside this one.
 */

const JOBWORK_LABEL: Record<JobWorkStatus, string> = {
  DRAFT: "Draft",
  APPROVED: "Approved",
  ISSUED: "At vendor",
  PARTIALLY_RECEIVED: "Partly received",
  RECEIVED: "Received",
  CLOSED: "Closed",
  CANCELLED: "Cancelled",
};

const JOBWORK_REGISTERED: Partial<Record<JobWorkStatus, StatusWord>> = {
  DRAFT: "draft",
  APPROVED: "approved",
  RECEIVED: "received",
  CLOSED: "closed",
  CANCELLED: "cancelled",
};

const JOBWORK_EXPLICIT_TONE: Partial<Record<JobWorkStatus, StatusTone>> = {
  ISSUED: "info",
  PARTIALLY_RECEIVED: "warning",
};

export function JobWorkStatusPill({ status }: { status: JobWorkStatus }) {
  const word = JOBWORK_REGISTERED[status];
  if (word) return <StatusBadge status={word} label={JOBWORK_LABEL[status]} />;
  return <StatusBadge tone={JOBWORK_EXPLICIT_TONE[status] ?? "neutral"} label={JOBWORK_LABEL[status]} />;
}

/** Past its due date with material still at the vendor. The words carry the meaning, never colour alone. */
export function OverduePill({ days }: { days: number }) {
  return <StatusBadge tone="danger" label={days === 1 ? "1 day overdue" : `${days} days overdue`} />;
}

/* ------------------------------------ e-way bill status ------------------------------------ */
/** What this module asked finance for, and what finance later answered — not a status operations
 *  decides. `NOT_REQUIRED` is shown quietly. */

const EWAY_LABEL: Record<EwayStatus, string> = {
  NOT_REQUIRED: "E-way bill not required",
  REQUESTED: "E-way bill requested",
  GENERATED: "E-way bill generated",
  FAILED: "E-way bill failed",
};

const EWAY_TONE: Record<EwayStatus, StatusTone> = {
  NOT_REQUIRED: "neutral",
  REQUESTED: "info",
  GENERATED: "success",
  FAILED: "danger",
};

export function EwayStatusPill({ status }: { status: EwayStatus }) {
  return <StatusBadge tone={EWAY_TONE[status]} label={EWAY_LABEL[status]} />;
}

/* ------------------------------ reconciliation verdict ------------------------------ */
/** Whether a job-work order's documents settle and agree with the ledger — a verdict, so it is
 *  a pill with words: "Balanced" (success), "Not balanced" (danger), "Not issued yet" (neutral). */
export function ReconciliationPill({ issued, balanced }: { issued: boolean; balanced: boolean }) {
  if (!issued) return <StatusBadge tone="neutral" label="Not issued yet" />;
  return balanced ? (
    <StatusBadge tone="success" label="Balanced" />
  ) : (
    <StatusBadge tone="danger" label="Not balanced" />
  );
}
