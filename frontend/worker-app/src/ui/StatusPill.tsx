import { StatusBadge } from "@opsgrids/ui";
import type { StatusTone, StatusWord } from "@opsgrids/ui";

import type { OfferState, PackageStatus, PickTaskStatus } from "@/api/types";

/*
 * Thin wrappers over the shared `StatusBadge`, the same pattern `product-operations`' own
 * `StatusPill.tsx` uses for the dispatcher board — copied rather than imported (this app has no
 * dependency on `product-operations`) but kept word-for-word the same tone mapping, so a status
 * reads identically on both surfaces.
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

/** Pick-task lifecycle (ADR-0025 §2). */
export function PickTaskStatusPill({ status }: { status: PickTaskStatus }) {
  const word = TASK_REGISTERED[status];
  if (word) return <StatusBadge status={word} label={TASK_LABEL[status]} />;
  return <StatusBadge tone={TASK_EXPLICIT_TONE[status] ?? "neutral"} label={TASK_LABEL[status]} />;
}

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

/** ADR-0025 §3's offer state machine. */
export function AssignmentOfferStatusPill({ state }: { state: OfferState }) {
  const word = OFFER_REGISTERED[state];
  if (word) return <StatusBadge status={word} label={OFFER_LABEL[state]} />;
  return <StatusBadge tone={OFFER_EXPLICIT_TONE[state] ?? "neutral"} label={OFFER_LABEL[state]} />;
}

const PACKAGE_LABEL: Record<PackageStatus, string> = {
  OPEN: "Open",
  PACKED: "Packed",
};

/** A package has no registered `StatusBadge` word — F3 has no separate open/confirm sequence
 *  (`PackageIn`'s own docstring), so `OPEN` is a transient value this app's own confirm call
 *  never leaves visible for long; it still renders honestly if it is ever read mid-flight. */
export function PackageStatusPill({ status }: { status: PackageStatus }) {
  return <StatusBadge tone={status === "PACKED" ? "success" : "neutral"} label={PACKAGE_LABEL[status]} />;
}
