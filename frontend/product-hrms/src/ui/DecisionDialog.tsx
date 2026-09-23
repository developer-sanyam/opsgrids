import { useEffect, useLayoutEffect, useRef, useState } from "react";

import { ApiError } from "@opsgrids/client";
import { Button, Dialog, InlineAlert, TextArea, formatInZone, resolveZone } from "@opsgrids/ui";
import type { ZoneLookup } from "@opsgrids/ui";

import { decideRequest } from "@/api/approvals";
import type { RequestDetailOut } from "@/api/types";
import { isLeaveSummary } from "@/lib/approvalCopy";
import { attendanceStatusLabel } from "@/lib/format";
import { formatLeaveRange, formatHalfDays } from "@/lib/leaveCopy";

// §4.B: "Comment (optional) — multiline, 500 characters" — the server's own limit is 2000
// (`comment` on `POST .../decision`), but the screen spec fixes this field's limit at 500; the
// counter keeps the same 90%-of-max point the field used at the old limit.
const COMMENT_MAX = 500;
const COMMENT_COUNTER_FROM = 450;

interface DecisionDialogProps {
  open: boolean;
  onClose: () => void;
  outcome: "approve" | "reject";
  request: RequestDetailOut;
  zoneLookup: ZoneLookup | null;
  onDecided: (detail: RequestDetailOut) => void;
  /** 409 — someone else decided first while the dialog was open. */
  onConflict: (typedComment: string) => void;
  /** 403 — eligibility was lost between opening the panel and confirming. */
  onIneligible: (detail: string) => void;
}

/**
 * §4.B — the confirmation dialog every decision goes through. Decisions are final: there is no
 * cancellation once sent and no re-decision, which is why the body names the consequence before
 * the caller confirms it.
 */
export function DecisionDialog({
  open,
  onClose,
  outcome,
  request,
  zoneLookup,
  onDecided,
  onConflict,
  onIneligible,
}: DecisionDialogProps) {
  const [comment, setComment] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | undefined>(undefined);
  const [offline, setOffline] = useState(false);

  const fieldContainerRef = useRef<HTMLDivElement>(null);
  const commentFieldRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) return;
    setComment("");
    setSubmitting(false);
    setFormError(undefined);
    setOffline(false);
  }, [open, outcome]);

  // A layout effect, not a passive one: `Dialog`'s own focus effect (`useEffect`, keyed on
  // `open`) reads `initialFocusRef.current` as soon as it mounts, and React fires every layout
  // effect in a commit before any passive effect runs, regardless of which component is the
  // "parent" — a plain `useEffect` here would lose the race and leave focus on the heading.
  useLayoutEffect(() => {
    if (!open) return;
    commentFieldRef.current =
      outcome === "reject" ? (fieldContainerRef.current?.querySelector("textarea") ?? null) : null;
  }, [open, outcome]);

  const zone = resolveZone(
    request.requester_location_code,
    zoneLookup ?? { locations: {}, organizationZone: "UTC" },
  ).zone;
  const summary = request.summary;
  const requesterName = request.requester_name ?? "This employee";
  const changeDescription = isLeaveSummary(summary)
    ? `${requesterName}'s leave for ${formatLeaveRange(summary.start_date, summary.end_date)} (${formatHalfDays(summary.total_half_days)})`
    : `${requesterName}'s attendance for ${formatInZone(`${summary.date}T00:00:00Z`, zone, "date")} ${outcome === "approve" ? `will change from ${attendanceStatusLabel(summary.from)} to ${attendanceStatusLabel(summary.to)}` : `stays ${attendanceStatusLabel(summary.from)}`}`;

  async function confirm() {
    if (submitting) return;
    setFormError(undefined);
    if (!navigator.onLine) {
      setOffline(true);
      return;
    }
    setSubmitting(true);
    try {
      const detail = await decideRequest(request.id, {
        outcome,
        comment: comment.trim() ? comment.trim() : null,
      });
      onDecided(detail);
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 409) {
          onConflict(comment);
          return;
        }
        if (err.status === 403) {
          onIneligible(err.problem?.detail ?? err.message);
          return;
        }
        setFormError(err.problem?.detail ?? err.message);
        return;
      }
      setFormError("Something went wrong. Try again.");
    } finally {
      setSubmitting(false);
    }
  }

  const noun = isLeaveSummary(summary) ? "leave request" : "correction";
  const title = outcome === "approve" ? `Approve this ${noun}?` : `Reject this ${noun}?`;
  const body = `${changeDescription}. This can't be undone.`;
  const helper =
    outcome === "approve"
      ? "The requester can see this."
      : `Tell ${requesterName} why. They'll see this on their request.`;

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={title}
      description={body}
      initialFocusRef={commentFieldRef}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={submitting}>
            Cancel
          </Button>
          <Button
            variant={outcome === "approve" ? "primary" : "danger"}
            onClick={() => void confirm()}
            disabled={submitting}
          >
            {submitting
              ? outcome === "approve"
                ? "Approving…"
                : "Rejecting…"
              : outcome === "approve"
                ? "Approve"
                : "Reject"}
          </Button>
        </>
      }
    >
      <div ref={fieldContainerRef} className="flex flex-col gap-4">
        <TextArea
          label="Comment"
          optional
          helper={helper}
          value={comment}
          onChange={(e) => setComment(e.target.value)}
          maxLength={COMMENT_MAX}
          counterFrom={COMMENT_COUNTER_FROM}
        />
        {offline ? (
          <InlineAlert tone="warning" title="You're offline. Reconnect to send this decision." />
        ) : null}
        {formError ? (
          <InlineAlert
            tone="danger"
            title={formError}
            action={
              <Button variant="outline" size="sm" onClick={() => void confirm()}>
                Try again
              </Button>
            }
          />
        ) : null}
      </div>
    </Dialog>
  );
}
