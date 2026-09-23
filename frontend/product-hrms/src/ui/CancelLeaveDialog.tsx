/**
 * `POST /leave/requests/{id}/cancel` (or `/force-cancel` for HR — `forCancel` picks which). A
 * pending request is simply withdrawn; an approved one posts a reversal to the ledger — the
 * confirmation says which is about to happen, since the two are meaningfully different once a
 * balance has already moved.
 */
import { useEffect, useState } from "react";

import { ApiError } from "@opsgrids/client";
import { Button, Dialog, InlineAlert, TextArea } from "@opsgrids/ui";

import { cancelLeaveRequest, forceCancelLeaveRequest } from "@/api/leave";
import type { LeaveRequestOut } from "@/api/types";

interface CancelLeaveDialogProps {
  open: boolean;
  onClose: () => void;
  request: LeaveRequestOut;
  /** HR's override (`hrms.time.leave_request.manage`) vs. the employee's own withdrawal. */
  forceCancel?: boolean | undefined;
  onCancelled: (updated: LeaveRequestOut) => void;
}

export function CancelLeaveDialog({
  open,
  onClose,
  request,
  forceCancel = false,
  onCancelled,
}: CancelLeaveDialogProps) {
  const [reason, setReason] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | undefined>(undefined);

  useEffect(() => {
    if (!open) return;
    setReason("");
    setSubmitting(false);
    setFormError(undefined);
  }, [open, request.id]);

  async function confirm() {
    if (submitting) return;
    setFormError(undefined);
    setSubmitting(true);
    try {
      const body = { reason: reason.trim() || null };
      const updated = forceCancel
        ? await forceCancelLeaveRequest(request.id, body)
        : await cancelLeaveRequest(request.id, body);
      onCancelled(updated);
    } catch (err) {
      setFormError(err instanceof ApiError ? (err.problem?.detail ?? err.message) : "Something went wrong. Try again.");
    } finally {
      setSubmitting(false);
    }
  }

  const willReverse = request.status === "approved";

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Cancel this leave request?"
      description={
        willReverse
          ? `This leave was already approved. Cancelling it credits ${request.total_half_days} half-day(s) back to the balance.`
          : "This request hasn't been decided yet. Withdrawing it removes it from the approver's queue."
      }
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={submitting}>
            Keep it
          </Button>
          <Button variant="danger" onClick={() => void confirm()} disabled={submitting}>
            {submitting ? "Cancelling…" : "Cancel request"}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
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
        <TextArea
          label="Reason"
          optional
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          maxLength={500}
        />
      </div>
    </Dialog>
  );
}
