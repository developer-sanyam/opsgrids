import type { ReactNode } from "react";
import { Link } from "react-router-dom";

import { Button, Tag, formatInZone, resolveZone } from "@opsgrids/ui";
import type { ZoneLookup } from "@opsgrids/ui";

import type { AttendanceRecord } from "@/api/types";

const TEN_MINUTES_MS = 10 * 60 * 1000;

/** §4.C, §9.1 C3 (corrected) — the requester's own words, on their own row only: truncated to one
 *  line, full text in `title` and in full on the request detail. Never rendered on someone else's
 *  row — that is a density decision (4,000-row tables), not an access one; the request detail is
 *  one activation away for anyone whose read scope reaches the record. */
function ReasonLine({ reason }: { reason: string }) {
  return (
    <span className="block max-w-56 truncate text-xs text-muted" title={reason}>
      {reason}
    </span>
  );
}

interface CorrectionCellProps {
  record: AttendanceRecord;
  /** Only the caller's own record ever shows the button (§4.C). */
  isOwn: boolean;
  zoneLookup: ZoneLookup | null;
  onRequestCorrection: (record: AttendanceRecord) => void;
  /** Injectable for tests — the "still waiting" threshold is wall-clock elapsed time, not a due
   *  date (§3.1, O6): the client never computes a deadline, only how long a fact has been true. */
  now?: Date;
}

/**
 * §4.C's "Correction" column — every state `AttendanceOut.regularization` can report, on any
 * row (the field is readable regardless of ownership; only the *action* is own-only).
 */
export function CorrectionCell({
  record,
  isOwn,
  zoneLookup,
  onRequestCorrection,
  now = new Date(),
}: CorrectionCellProps) {
  const reg = record.regularization;
  const zone = resolveZone(
    record.location_code,
    zoneLookup ?? { locations: {}, organizationZone: "UTC" },
  ).zone;

  if (!reg) {
    if (isOwn && record.status !== "regularized") {
      return (
        <Button size="sm" variant="outline" onClick={() => onRequestCorrection(record)}>
          Request a correction
        </Button>
      );
    }
    return null;
  }

  if (reg.status === "pending") {
    const link = reg.approval_request_id ? `/approvals/requests/${reg.approval_request_id}` : null;

    // Tri-state (§4.C, item 2 — extended by the routed-approver fields): `approval_opened_at`
    // says whether routing has happened *at all*; `approver_unassigned` (only meaningful once
    // opened) tells "nobody found" apart from "found someone". The UI never invents an approver
    // it wasn't given (§3.1, O6) — a null `approver_name` on an assigned-but-unnamed step (a
    // legacy event, or one replayed without the key) renders no name, not a guess.
    let statusBody: ReactNode;
    if (reg.approval_opened_at === null) {
      const elapsedMs = now.getTime() - new Date(reg.created_at).getTime();
      statusBody = (
        <>
          <Tag>Waiting to be routed</Tag>
          <span className="text-xs text-muted">
            {elapsedMs > TEN_MINUTES_MS
              ? "Still waiting to be routed. Contact HR if this doesn't clear."
              : "This usually takes a few seconds."}
          </span>
        </>
      );
    } else if (reg.approver_unassigned) {
      statusBody = (
        <>
          <Tag tone="warning">Correction pending</Tag>
          <span className="text-xs text-muted">Unassigned — no approver found</span>
        </>
      );
    } else {
      statusBody = (
        <>
          <Tag>Correction pending</Tag>
          {reg.approver_name ? (
            <span className="text-xs text-muted">Waiting on {reg.approver_name}</span>
          ) : null}
        </>
      );
    }

    const body = (
      <span className="flex flex-col items-end gap-0.5 text-right">
        {statusBody}
        {isOwn ? <ReasonLine reason={reg.reason} /> : null}
      </span>
    );
    return link ? (
      <Link to={link} className="relative hit-44 focus-visible:outline-none">
        {body}
      </Link>
    ) : (
      body
    );
  }

  if (reg.status === "rejected") {
    // The decider's name and comment (design QA gap) — the comment stays truncated with the full
    // text in `title` and in full on the request detail, the same treatment `ReasonLine` gives
    // the requester's own words.
    const body = (
      <span className="flex flex-col items-end gap-0.5 text-right">
        <Tag tone="negative">Correction rejected</Tag>
        {reg.decided_by_name ? (
          <span className="text-xs text-muted">by {reg.decided_by_name}</span>
        ) : null}
        {reg.decision_comment ? (
          <span
            className="block max-w-56 truncate text-xs text-muted italic"
            title={reg.decision_comment}
          >
            “{reg.decision_comment}”
          </span>
        ) : null}
        {isOwn ? <ReasonLine reason={reg.reason} /> : null}
      </span>
    );
    return reg.approval_request_id ? (
      <Link to={`/approvals/requests/${reg.approval_request_id}`} className="relative hit-44">
        {body}
      </Link>
    ) : (
      body
    );
  }

  if (reg.status === "failed") {
    // "Approved — the correction couldn't be applied" (§2.10): the decision stands, the record
    // just didn't move. Never "error", never "approval failed".
    const body = (
      <span className="flex flex-col items-end gap-0.5 text-right" title={reg.failure_reason ?? undefined}>
        <Tag tone="warning">Couldn't be applied</Tag>
        {isOwn ? <ReasonLine reason={reg.reason} /> : null}
      </span>
    );
    return reg.approval_request_id ? (
      <Link to={`/approvals/requests/${reg.approval_request_id}`} className="relative hit-44">
        {body}
      </Link>
    ) : (
      body
    );
  }

  // "approved" — the consumer only ever marks a request "approved" once the record itself has
  // moved (`services.apply_regularization_decision`), so this state and the record's own status
  // change in lockstep. The status pill already carries "Regularised"; this line just says when.
  return (
    <span className="flex flex-col items-end gap-0.5 text-right text-xs text-muted">
      <span>Corrected on {formatInZone(reg.decided_at ?? reg.created_at, zone, "date")}</span>
      {isOwn ? <ReasonLine reason={reg.reason} /> : null}
    </span>
  );
}
