import type { LotQualityStatus, RollState } from "@/api/trackingTypes";
import type { StockStatus } from "@/api/types";
import { LOT_QUALITY_LABEL, ROLL_STATE_LABEL, STOCK_STATUS_LABEL } from "@/lib/trackingLabels";
import { StatusBadge } from "@opsgrids/ui";
import type { StatusTone } from "@opsgrids/ui";

/*
 * Stock statuses, lot quality and roll states are this product's own words, not the shared
 * registry's, so each takes an explicit tone — and always the word beside it, so the colour is
 * never the only carrier of meaning.
 */

const STOCK_TONE: Record<StockStatus, StatusTone> = {
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

export function StockStatusBadge({ status }: { status: StockStatus }) {
  return <StatusBadge tone={STOCK_TONE[status]} label={STOCK_STATUS_LABEL[status]} />;
}

const QUALITY_TONE: Record<LotQualityStatus, StatusTone> = {
  APPROVED: "success",
  PENDING: "warning",
  ON_HOLD: "warning",
  REJECTED: "danger",
};

export function LotQualityBadge({ status }: { status: LotQualityStatus }) {
  return <StatusBadge tone={QUALITY_TONE[status]} label={LOT_QUALITY_LABEL[status]} />;
}

const ROLL_TONE: Record<RollState, StatusTone> = {
  ACTIVE: "success",
  SPLIT: "neutral",
  MERGED: "neutral",
};

export function RollStateBadge({ state }: { state: RollState }) {
  return <StatusBadge tone={ROLL_TONE[state]} label={ROLL_STATE_LABEL[state]} />;
}
