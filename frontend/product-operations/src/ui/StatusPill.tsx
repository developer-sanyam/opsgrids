import type { PoStatus, SupplierStatus } from "@/api/types";
import { AlertTriangleIcon, StatusBadge } from "@opsgrids/ui";
import type { StatusTone } from "@opsgrids/ui";

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
