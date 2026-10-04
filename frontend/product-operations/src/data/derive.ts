/**
 * Pure rollups over the Operations read models. No I/O, no React — just sums, grouping and
 * percentages — so the dashboard's KPIs, the spend-by-category bars, the PO-status donut and the
 * "needs attention" list all derive from the same purchase-order / inventory / supplier payloads.
 * Kept pure so it is trivially testable.
 */
import type {
  AttentionItem,
  Category,
  CategorySpend,
  InventoryItemOut,
  PoStatus,
  PurchaseOrderOut,
  SpendPoint,
  StatusSlice,
  SupplierOut,
  SupplierSpend,
} from "@/api/types";
import { daysBetween } from "@/lib/format";

/** Calendar-month labels, indexed 0 (Jan) to 11 (Dec) — mirrors the server's `MONTH_LABELS`. */
const MONTH_LABELS = [
  "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
] as const;

/** Human labels for the procurement categories. */
export const CATEGORY_LABEL: Record<Category, string> = {
  raw_materials: "Raw materials",
  packaging: "Packaging",
  components: "Components",
  mro: "MRO",
  logistics: "Logistics",
  services: "Services",
};

/** Human labels for the PO lifecycle. */
export const PO_STATUS_LABEL: Record<PoStatus, string> = {
  draft: "Draft",
  approved: "Approved",
  partially_received: "Partially received",
  received: "Received",
  closed: "Closed",
  cancelled: "Cancelled",
};

/** A purchase order's total in minor units — summed from its line items, never stored. */
export function poTotalMinor(po: PurchaseOrderOut): number {
  return po.line_items.reduce((sum, l) => sum + l.quantity * l.unit_price_minor, 0);
}

/** Total quantity of units across a PO's lines. */
export function poItemCount(po: PurchaseOrderOut): number {
  return po.line_items.length;
}

/** "Open" purchase orders are the ones still in flight: draft or approved. */
export function isOpen(po: PurchaseOrderOut): boolean {
  return po.status === "draft" || po.status === "approved";
}

/** How many purchase orders are open (draft or approved). */
export function openPoCount(pos: readonly PurchaseOrderOut[]): number {
  return pos.filter(isOpen).length;
}

/** Total value of stock on hand, in minor units. */
export function inventoryValueMinor(items: readonly InventoryItemOut[]): number {
  return items.reduce((sum, it) => sum + it.on_hand * it.unit_value_minor, 0);
}

/** An item is low when at or below its reorder point; out when nothing is on hand. */
export function isLowStock(item: InventoryItemOut): boolean {
  return item.on_hand <= item.reorder_point;
}
export function isOutOfStock(item: InventoryItemOut): boolean {
  return item.on_hand === 0;
}

/** How many items have nothing on hand — fixture-mode mirror of the server's `out_of_stock_count`. */
export function outOfStockCount(items: readonly InventoryItemOut[]): number {
  return items.filter(isOutOfStock).length;
}

/** The items at or below their reorder point, most-depleted first. */
export function lowStockItems(items: readonly InventoryItemOut[]): InventoryItemOut[] {
  return items
    .filter(isLowStock)
    .slice()
    .sort((a, b) => a.on_hand / a.reorder_point - b.on_hand / b.reorder_point);
}

/** An open PO is late when its expected date has passed and nothing has been received. */
export function isLate(po: PurchaseOrderOut, today: string): boolean {
  return isOpen(po) && po.received_date === null && daysBetween(po.expected_date, today) < 0;
}

/** Open POs past their expected date, most overdue first. */
export function lateDeliveries(
  pos: readonly PurchaseOrderOut[],
  today: string,
): PurchaseOrderOut[] {
  return pos
    .filter((po) => isLate(po, today))
    .slice()
    .sort((a, b) => daysBetween(a.expected_date, today) - daysBetween(b.expected_date, today));
}

function pct(part: number, whole: number): number {
  return whole === 0 ? 0 : Math.round((part / whole) * 100);
}

/**
 * Real, spend-weighted on-time delivery rate, computed fresh from receipts — never read off a
 * supplier's own `on_time_pct`, which is set once at creation and never revisited by a receipt
 * (dashboard-data audit §3.5 row 13). Mirrors the server's `domain.on_time_delivery_pct`.
 * `null`, not `0`, until this organization has received a purchase order: a 0% rate would claim
 * every delivery was late, when in fact none has landed to judge.
 */
export function onTimeDeliveryPct(pos: readonly PurchaseOrderOut[]): number | null {
  const received = pos.filter((po) => po.received_date !== null);
  const totalSpend = received.reduce((sum, po) => sum + poTotalMinor(po), 0);
  if (totalSpend === 0) return null;
  const onTimeSpend = received
    .filter((po) => po.received_date !== null && po.received_date <= po.expected_date)
    .reduce((sum, po) => sum + poTotalMinor(po), 0);
  return Math.round((onTimeSpend / totalSpend) * 1000) / 10;
}

/**
 * Committed spend grouped by category, largest first. Draft and cancelled POs are excluded —
 * spend is only committed once a PO is approved, and a cancelled PO committed nothing in the end.
 * Percentages are shares of the resolved total.
 */
export function spendByCategory(pos: readonly PurchaseOrderOut[]): CategorySpend[] {
  const committed = pos.filter((po) => po.status !== "draft" && po.status !== "cancelled");
  const totals = new Map<Category, number>();
  for (const po of committed) {
    totals.set(po.category, (totals.get(po.category) ?? 0) + poTotalMinor(po));
  }
  const grand = [...totals.values()].reduce((a, b) => a + b, 0);
  return [...totals.entries()]
    .map(([key, spend_minor]) => ({
      key,
      name: CATEGORY_LABEL[key],
      spend_minor,
      pct: pct(spend_minor, grand),
    }))
    .sort((a, b) => b.spend_minor - a.spend_minor);
}

const STATUS_ORDER: PoStatus[] = ["draft", "approved", "received", "closed", "cancelled"];

/** Purchase orders grouped by status, in lifecycle order, as donut slices. */
export function poStatusMix(pos: readonly PurchaseOrderOut[]): StatusSlice[] {
  const total = pos.length;
  return STATUS_ORDER.map((key) => {
    const count = pos.filter((po) => po.status === key).length;
    return { key, label: PO_STATUS_LABEL[key], count, pct: pct(count, total) };
  }).filter((slice) => slice.count > 0);
}

/**
 * Committed spend per calendar month, oldest first — the trend chart's real series. Mirrors the
 * server's `domain.spend_trend`. Committed POs (anything past draft, excluding cancelled) are
 * bucketed by `created_date`'s year and month; only the most recent `months` are kept.
 */
export function spendTrend(pos: readonly PurchaseOrderOut[], months = 12): SpendPoint[] {
  const committed = pos.filter((po) => po.status !== "draft" && po.status !== "cancelled");
  const byMonth = new Map<string, { month: number; spend_minor: number }>();
  for (const po of committed) {
    const [year, month] = po.created_date.split("-");
    const key = `${year}-${month}`;
    const existing = byMonth.get(key);
    byMonth.set(key, {
      month: Number(month),
      spend_minor: (existing?.spend_minor ?? 0) + poTotalMinor(po),
    });
  }
  return [...byMonth.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .slice(-months)
    .map(([, { month, spend_minor }]) => ({ label: MONTH_LABELS[month - 1] ?? "", spend_minor }));
}

/** Suppliers by lifetime spend, largest first, with each one's share of the total. Mirrors the
 *  server's `domain.top_suppliers`. */
export function topSuppliers(suppliers: readonly SupplierOut[], limit = 6): SupplierSpend[] {
  const grand = suppliers.reduce((sum, s) => sum + s.spend_minor, 0);
  return suppliers
    .slice()
    .sort((a, b) => b.spend_minor - a.spend_minor)
    .slice(0, limit)
    .map((s) => ({
      id: s.id,
      name: s.name,
      category: s.category,
      spend_minor: s.spend_minor,
      pct: pct(s.spend_minor, grand),
    }));
}

/**
 * The "needs attention" queue: out-of-stock and low-stock items, plus late deliveries, ordered
 * by severity. Each item names what is wrong and links to where it can be fixed.
 */
export function attentionItems(
  pos: readonly PurchaseOrderOut[],
  inventory: readonly InventoryItemOut[],
  today: string,
): AttentionItem[] {
  const stock: AttentionItem[] = lowStockItems(inventory).map((item) => {
    const out = isOutOfStock(item);
    return {
      id: `stock-${item.id}`,
      kind: out ? "out_of_stock" : "low_stock",
      title: item.name,
      detail: out
        ? `Out of stock at ${item.warehouse}`
        : `${item.on_hand} ${item.uom} on hand · reorder at ${item.reorder_point}`,
      href: "/inventory",
      tone: out ? "danger" : "late",
    };
  });

  const late: AttentionItem[] = lateDeliveries(pos, today).map((po) => {
    const overdue = Math.abs(daysBetween(po.expected_date, today));
    return {
      id: `late-${po.id}`,
      kind: "late_delivery",
      title: `${po.po_number} · ${po.supplier_name}`,
      detail: `${overdue} day${overdue === 1 ? "" : "s"} overdue · expected at ${po.warehouse}`,
      href: `/purchase-orders/${po.id}`,
      tone: "danger",
    };
  });

  // Danger (out-of-stock, late) first, then low-stock warnings.
  return [...late, ...stock].sort((a, b) => {
    const rank = (t: AttentionItem["tone"]) => (t === "danger" ? 0 : 1);
    return rank(a.tone) - rank(b.tone);
  });
}
