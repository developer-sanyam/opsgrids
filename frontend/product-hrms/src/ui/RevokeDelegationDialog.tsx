import { useEffect, useState } from "react";

import { ApiError } from "@opsgrids/client";
import { Button, Dialog, InlineAlert } from "@opsgrids/ui";

import { revokeDelegation } from "@/api/approvals";
import type { DelegationOut } from "@/api/types";
import { revokeConfirmationBody } from "@/lib/delegationCopy";

interface RevokeDelegationDialogProps {
  open: boolean;
  onClose: () => void;
  delegation: DelegationOut;
  myEmployeeId: string | null;
  onRevoked: (revoked: DelegationOut) => void;
  /** 409 — a second revoke, already applied elsewhere (another tab, another admin). */
  onAlreadyRevoked: () => void;
}

/**
 * §4.E — the revoke confirmation. Revoking is immediate and final: no re-route, no undo, and the
 * delegator never lost eligibility while it ran — the body says so honestly rather than implying
 * anything "comes back".
 */
export function RevokeDelegationDialog({
  open,
  onClose,
  delegation,
  myEmployeeId,
  onRevoked,
  onAlreadyRevoked,
}: RevokeDelegationDialogProps) {
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | undefined>(undefined);
  const [offline, setOffline] = useState(false);

  useEffect(() => {
    if (!open) return;
    setSubmitting(false);
    setFormError(undefined);
    setOffline(false);
  }, [open, delegation.id]);

  async function confirm() {
    if (submitting) return;
    setFormError(undefined);
    if (!navigator.onLine) {
      setOffline(true);
      return;
    }
    setSubmitting(true);
    try {
      const revoked = await revokeDelegation(delegation.id);
      onRevoked(revoked);
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 409) {
          onAlreadyRevoked();
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

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Revoke this delegation?"
      description={revokeConfirmationBody(delegation, myEmployeeId)}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={submitting}>
            Keep it
          </Button>
          <Button variant="danger" onClick={() => void confirm()} disabled={submitting}>
            {submitting ? "Revoking…" : "Revoke"}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        {offline ? (
          <InlineAlert tone="warning" title="You're offline. Reconnect to send this." />
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
