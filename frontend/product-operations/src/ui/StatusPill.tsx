import type { OfferState, OrderStatus, PickTaskStatus, PoStatus, SupplierStatus } from "@/api/types";
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
  received: "Received",
  closed: "Closed",
  cancelled: "Cancelled",
};

/** Purchase-order lifecycle. */
export function PoStatusPill({ status }: { status: PoStatus }) {
  return <StatusBadge status={status} label={PO_LABEL[status]} />;
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
