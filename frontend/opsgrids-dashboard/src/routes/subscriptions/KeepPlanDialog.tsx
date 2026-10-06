import { useState } from "react";

import { ApiError, problemMessage } from "@opsgrids/client";
import { ConfirmDialog } from "@opsgrids/ui";

import { changeSubscriptionPlan, previewSubscriptionPlanChange } from "@/api";
import type { Subscription, SubscriptionPlanSummary } from "@/api/types";
import { formatDate } from "@/lib/money";
import { problemCode } from "@/lib/problemCode";
import { KEEP_STALE_MESSAGE, failureMessage, planName } from "@/lib/subscriptionCopy";

interface Props {
  subscription: Subscription;
  product: string;
  summary: SubscriptionPlanSummary;
  onClose: () => void;
  onKept: (message: string) => void;
  /**
   * The row was out of date: the change already happened or was withdrawn. The page closes this,
   * says so, and reads the rows again — there is no list in this dialog to "choose again" from.
   */
  onStale: (message: string) => void;
}

/** Answers that mean "this row is behind": the scheduled change is no longer what the row says. */
const STALE = new Set(["state_changed", "not_offerable", "already_pinned"]);

/**
 * "Keep current plan": withdraw the change waiting for the period end.
 *
 * The customer change-plan supports it directly — re-pinning the plan already in force cancels the
 * waiting change (subscription's `withdraw` branch, kept even when that plan is no longer on offer).
 * The confirm asks the server first and goes ahead only if its answer is exactly that, carrying the
 * preview's `expected` state so a change the billing run applied in the meantime is refused rather
 * than silently undone. A change the OpsGrids team scheduled is not offered here at all (the server
 * refuses it, `scheduled_by_opsgrids`).
 */
export function KeepPlanDialog({ subscription, product, summary, onClose, onKept, onStale }: Props) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<{ message: string; reference: string | undefined } | null>(null);

  const current = planName(summary.current);
  const next = planName(summary.pending);
  const when = summary.pending_effective_at ? ` on ${formatDate(summary.pending_effective_at)}` : "";

  async function keep() {
    if (pending) return;
    setPending(true);
    setError(null);
    try {
      const answer = await previewSubscriptionPlanChange(subscription.id, subscription.plan_version_id);
      if (answer.refusal_code === null && answer.action !== "withdraw_scheduled") {
        onStale(KEEP_STALE_MESSAGE);
        return;
      }
      if (answer.refusal_code !== null) {
        if (STALE.has(answer.refusal_code)) {
          onStale(KEEP_STALE_MESSAGE);
          return;
        }
        setError({
          message: failureMessage(answer.refusal_code, answer.refusal_detail, "The scheduled change can't be cancelled."),
          reference: undefined,
        });
        return;
      }
      await changeSubscriptionPlan(subscription.id, subscription.plan_version_id, answer.expected);
      onKept(`${product} stays on ${current}.`);
      onClose();
    } catch (err) {
      const code = problemCode(err);
      if (code !== null && STALE.has(code)) {
        onStale(KEEP_STALE_MESSAGE);
        return;
      }
      setError({
        message: failureMessage(code, problemMessage(err), "The scheduled change can't be cancelled."),
        reference: err instanceof ApiError ? err.problem?.request_id : undefined,
      });
    } finally {
      setPending(false);
    }
  }

  return (
    <ConfirmDialog
      open
      onClose={onClose}
      onConfirm={() => void keep()}
      tone="primary"
      title={`Keep ${product} on ${current}?`}
      body={`The change to ${next} scheduled${when} is cancelled. ${product} stays on ${current}, and nothing is charged.`}
      confirmLabel="Keep current plan"
      pendingLabel="Saving…"
      cancelLabel="Back"
      pending={pending}
      {...(error ? { error: error.message } : {})}
      {...(error?.reference ? { reference: error.reference } : {})}
    />
  );
}
