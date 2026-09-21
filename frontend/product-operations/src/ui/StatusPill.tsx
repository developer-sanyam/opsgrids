import type { PoStatus, SupplierStatus } from "@/api/types";
import { AlertTriangleIcon, cn } from "@opsgrids/ui";

const PILL_BASE =
  "inline-flex items-center gap-1.5 rounded-full pl-2 pr-2.5 py-1 text-xs font-semibold";

/* ------------------------------ PO status ------------------------------ */

const PO_TONE: Record<PoStatus, string> = {
  draft: "bg-ground text-muted",
  approved: "bg-info-soft text-info",
  received: "bg-present-soft text-present",
  closed: "bg-cyan-soft text-cyan",
  cancelled: "bg-danger-soft text-danger",
};

const PO_LABEL: Record<PoStatus, string> = {
  draft: "Draft",
  approved: "Approved",
  received: "Received",
  closed: "Closed",
  cancelled: "Cancelled",
};

/** Purchase-order lifecycle. A dot backs up the colour so meaning never rests on colour alone. */
export function PoStatusPill({ status }: { status: PoStatus }) {
  return (
    <span className={cn(PILL_BASE, PO_TONE[status])}>
      <span className="size-1.5 rounded-full bg-current" aria-hidden />
      {PO_LABEL[status]}
    </span>
  );
}

/* --------------------------- supplier status --------------------------- */

const SUPPLIER_TONE: Record<SupplierStatus, string> = {
  active: "bg-present-soft text-present",
  on_hold: "bg-late-soft text-late",
  pending: "bg-info-soft text-info",
};

const SUPPLIER_LABEL: Record<SupplierStatus, string> = {
  active: "Active",
  on_hold: "On hold",
  pending: "Pending",
};

export function SupplierStatusPill({ status }: { status: SupplierStatus }) {
  return (
    <span className={cn(PILL_BASE, SUPPLIER_TONE[status])}>
      <span className="size-1.5 rounded-full bg-current" aria-hidden />
      {SUPPLIER_LABEL[status]}
    </span>
  );
}

/* ------------------------------ stock level ---------------------------- */

export type StockLevel = "ok" | "low" | "out";

/**
 * Stock health, always **labelled** — never colour alone. "Low" and "Out" carry a warning icon
 * and text; a healthy item shows a quiet neutral "In stock" so the column reads consistently.
 */
export function StockPill({ level }: { level: StockLevel }) {
  if (level === "ok") {
    return (
      <span className={cn(PILL_BASE, "bg-ground text-muted")}>
        <span className="size-1.5 rounded-full bg-present" aria-hidden />
        In stock
      </span>
    );
  }
  const danger = level === "out";
  return (
    <span
      className={cn(PILL_BASE, danger ? "bg-danger-soft text-danger" : "bg-late-soft text-late")}
    >
      <AlertTriangleIcon className="size-3.5" strokeWidth={2} />
      {danger ? "Out of stock" : "Low stock"}
    </span>
  );
}
