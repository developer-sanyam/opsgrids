import type { ReactNode } from "react";

import { USE_FIXTURES } from "@/api/client";
import type { EInvoiceState, ImsAction, ImsStatus } from "@/api/types";
import { actionLabel, filingView, imsStatusLabel } from "@/data/compliance";
import type { FilingStateKey } from "@/data/compliance";
import {
  AlertOctagonIcon,
  CheckCircleIcon,
  ClockIcon,
  CloseIcon,
  MinusCircleIcon,
  PauseCircleIcon,
  SampleChip,
  StatusBadge,
} from "@opsgrids/ui";
import type { StatusTone } from "@opsgrids/ui";

/**
 * Filing position, on the shared `StatusBadge`.
 *
 * Each state carries a glyph as well as a colour and a word, because a filing column is scanned
 * fast and by people who may not separate the greens from the ambers. Colour is the third cue
 * here, never the first. "filed", "failed", "cancelled" and "not_required" are registered words;
 * "in_flight" and "not_filed" are not, so they take an explicit tone override.
 */
const FILING_TONE: Record<FilingStateKey, StatusTone> = {
  filed: "success",
  in_flight: "info",
  not_filed: "warning",
  failed: "danger",
  cancelled: "danger",
  not_required: "neutral",
};

const FILING_ICON: Record<FilingStateKey, ReactNode> = {
  filed: <CheckCircleIcon className="size-3.5" />,
  in_flight: <ClockIcon className="size-3.5" />,
  not_filed: <ClockIcon className="size-3.5" />,
  failed: <AlertOctagonIcon className="size-3.5" />,
  cancelled: <MinusCircleIcon className="size-3.5" />,
  not_required: <MinusCircleIcon className="size-3.5" />,
};

/**
 * Where one document stands with the Invoice Registration Portal.
 *
 * Renders a quiet dash when the service reported no filing position at all — an unreported state
 * is not the same as an unmet obligation, and inventing "not filed" for a document nobody asked
 * about would put a false deadline on somebody's screen.
 */
export function FilingStatusPill({ state }: { state: EInvoiceState | undefined }) {
  const view = filingView(state);
  if (view === null) {
    return (
      <span className="text-faint" title="This service did not report a filing position">
        <span aria-hidden>—</span>
        <span className="sr-only">Filing position not reported</span>
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1.5" title={view.detail}>
      {FILING_ICON[view.key]}
      <StatusBadge tone={FILING_TONE[view.key]} label={view.label} />
    </span>
  );
}

/** "pending", "accepted" and "rejected" are registered words; "pending_action" is not. */
const IMS_TONE: Record<ImsStatus, StatusTone> = {
  pending: "warning",
  accepted: "success",
  rejected: "danger",
  pending_action: "info",
};

const IMS_ICON: Record<ImsStatus, ReactNode> = {
  pending: <ClockIcon className="size-3.5" />,
  accepted: <CheckCircleIcon className="size-3.5" />,
  rejected: <CloseIcon className="size-3.5" />,
  pending_action: <PauseCircleIcon className="size-3.5" />,
};

/** Where an inbound supplier document stands: undecided until a person decides it. */
export function ImsStatusPill({ status }: { status: ImsStatus }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      {IMS_ICON[status]}
      <StatusBadge tone={IMS_TONE[status]} label={imsStatusLabel(status)} />
    </span>
  );
}

/**
 * The offline marker.
 *
 * Renders nothing live, and an amber chip in fixture mode. A compliance screen is the last place a
 * fabricated figure should be able to pass for a real one: "we filed 12 of 14" is a statement
 * about a statutory obligation, and a reader has to be able to tell at a glance whether it came
 * from the portal or from a seed file.
 */
export function SampleDataChip() {
  if (!USE_FIXTURES) return null;
  return (
    <SampleChip
      label="Sample data"
      title="Fixture mode — these documents, filings and suggestions are seeded, not this organization's"
    />
  );
}

/**
 * What the platform proposes — always worded as a proposal.
 *
 * Neutral by design: an accept suggestion rendered in green and a reject in red would turn advice
 * into a verdict, and the whole point of this queue is that the machine suggests and the person
 * decides. The colour arrives only once somebody has actually decided.
 */
export function SuggestedAction({ action }: { action: ImsAction }) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full bg-ground px-2.5 py-1 text-xs font-semibold text-ink-soft whitespace-nowrap">
      <span className="font-medium text-muted">Suggests</span>
      {actionLabel(action)}
    </span>
  );
}
