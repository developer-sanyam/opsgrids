import type { LedgerRowOut, LotQualityStatus, OnSiteStatus, RollState } from "@/api/trackingTypes";
import type { StockStatus } from "@/api/types";

export const STOCK_STATUS_LABEL: Record<StockStatus, string> = {
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

/** The statuses a stock operation may move between, in the order a person thinks of them. */
export const ON_SITE_STATUSES: OnSiteStatus[] = [
  "AVAILABLE",
  "QUALITY_HOLD",
  "QUARANTINE",
  "DAMAGED",
  "REJECTED",
  "WIP",
  "BLOCKED",
];

/** A bucket whose status a stock operation may act on (not in transit, not at a job worker). */
export function isOnSiteRow<T extends { status: StockStatus }>(row: T): row is T & { status: OnSiteStatus } {
  return (ON_SITE_STATUSES as StockStatus[]).includes(row.status);
}

export const STOCK_STATUS_OPTIONS =ON_SITE_STATUSES.map((value) => ({
  value,
  label: STOCK_STATUS_LABEL[value],
}));

export const LOT_QUALITY_LABEL: Record<LotQualityStatus, string> = {
  PENDING: "Pending",
  APPROVED: "Approved",
  ON_HOLD: "On hold",
  REJECTED: "Rejected",
};

export const LOT_QUALITY_OPTIONS = (Object.keys(LOT_QUALITY_LABEL) as LotQualityStatus[]).map((value) => ({
  value,
  label: LOT_QUALITY_LABEL[value],
}));

export const ROLL_STATE_LABEL: Record<RollState, string> = {
  ACTIVE: "Active",
  SPLIT: "Split",
  MERGED: "Merged",
};

/** The ledger kinds the server accepts as a filter. */
export const LEDGER_KIND_OPTIONS = [
  { value: "opening", label: "Opening balance" },
  { value: "receipt", label: "Receipt" },
  { value: "adjustment", label: "Adjustment" },
  { value: "count", label: "Count" },
  { value: "relocation", label: "Relocation" },
  { value: "issue", label: "Issue" },
] as const;

/** Phase 1 posts four operations as `relocation` moves told apart by `reference_type` (ADR-0014 §12). */
const RELOCATION_LABEL: Record<string, string> = {
  bin_transfer: "Bin transfer",
  roll_split: "Roll split",
  roll_merge: "Roll merge",
  status_change: "Status change",
};

/** What a ledger row is called on screen: the operation for a relocation, the kind otherwise. */
export function ledgerRowLabel(row: Pick<LedgerRowOut, "kind" | "reference_type">): string {
  if (row.kind === "relocation") {
    const named = row.reference_type ? RELOCATION_LABEL[row.reference_type] : undefined;
    return named ?? "Relocation";
  }
  return LEDGER_KIND_OPTIONS.find((o) => o.value === row.kind)?.label ?? row.kind;
}

/** A roll event's kind (`SPLIT`, `MERGE_RESULT`, `STATUS_CHANGED`…) as a sentence fragment. */
export function rollEventLabel(kind: string): string {
  const spaced = kind.toLowerCase().replace(/_/g, " ");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}
