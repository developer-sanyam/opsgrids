import { useEffect, useState } from "react";

import { retryEwayBill } from "@/api/client";
import type { EwayState } from "@/api/types";
import { ewayStateView, formatCountdown } from "@/data/compliance";
import { ApiError, Can, problemMessage, useMutation } from "@opsgrids/client";
import { Button, ConfirmDialog, Tag } from "@opsgrids/ui";

/**
 * A wall clock that ticks once a second, and only while something is counting down.
 *
 * Not a shared global timer: a page of forty e-Way rows has at most a handful of retries running,
 * and each one owning its own one-second interval — cleared the moment it stops mattering — is
 * simpler than a store nobody else reads. `enabled = false` costs nothing.
 */
function useNow(enabled: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!enabled) return;
    setNow(Date.now());
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [enabled]);
  return now;
}

interface EwayStateCellProps {
  invoiceId: string;
  invoiceNumber: string;
  state: EwayState;
  /** Called after the server accepted a retry, so the page can read what happens next. */
  onRetried: () => void;
}

/**
 * One document's e-Way position while a generation is being retried or has failed: the state in
 * words, a countdown to the automatic retry, the portal's reason, what to do about it, and — where
 * it would be accepted and the caller may press it — "Retry now".
 *
 * Three rules this component keeps, each a way it is usually got wrong:
 *
 * **The state is said, not coloured.** "Retrying — attempt 3 of 8" and "Refused by the portal
 * (code 113)" are the tag's text; the tone only agrees with them. A refusal and a spent budget
 * read differently on purpose, because they call for opposite actions.
 *
 * **"Retry now" is offered only where the server says it would work** (`state.can_retry`) **and
 * the caller holds the permission** (`<Can>`, which hides and protects nothing — the endpoint
 * refuses on its own). A refusal offers no retry: the identical consignment would be refused
 * again, and the remedy text says what to fix instead.
 *
 * **It asks first, and never with `window.confirm`.** A retry calls a government portal on the
 * caller's behalf, so it goes through the same dialog every consequential action here does, with
 * focus on Cancel.
 */
export function EwayStateCell({ invoiceId, invoiceNumber, state, onRetried }: EwayStateCellProps) {
  const view = ewayStateView(state);
  const counting = state.status === "pending" && typeof state.retry_at === "string";
  const now = useNow(counting);
  const [confirming, setConfirming] = useState(false);
  const [requested, setRequested] = useState(false);

  // A newer attempt supersedes the acknowledgement of the last request.
  useEffect(() => {
    setRequested(false);
  }, [state.attempt, state.status]);

  const retry = useMutation(() => retryEwayBill(invoiceId), {
    onSuccess: () => {
      setConfirming(false);
      setRequested(true);
      onRetried();
    },
  });

  if (view === null) return null;

  const countdown =
    counting && typeof state.retry_at === "string" ? formatCountdown(state.retry_at, now) : null;

  return (
    <div className="flex flex-col items-end gap-1.5 text-right">
      <Tag className={view.tone}>{view.headline}</Tag>

      {countdown !== null ? (
        // `role="timer"` is implicitly `aria-live="off"`: a countdown that announced every second
        // would drown out everything else on the page.
        <p role="timer" className="text-xs text-ink-soft tnum">
          {countdown.due ? "Next attempt due any moment now" : `Next attempt in ${countdown.label}`}
        </p>
      ) : null}

      {view.detail !== null ? (
        <p className="max-w-xs text-xs text-muted">{view.detail}</p>
      ) : null}

      {state.error !== null && state.error !== undefined && state.error !== "" ? (
        <p className="max-w-xs text-xs text-faint">
          Portal said:{" "}
          <span className="money">
            {state.error_code !== null && state.error_code !== undefined
              ? `${state.error_code} — `
              : ""}
            {state.error}
          </span>
        </p>
      ) : null}

      {requested ? (
        <p role="status" className="text-xs font-medium text-present">
          Retry requested — the portal is being asked now.
        </p>
      ) : null}

      {state.can_retry === true && !requested ? (
        <Can permission="finance.compliance.eway.retry" scope="ORG">
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              retry.reset();
              setConfirming(true);
            }}
          >
            Retry now…
          </Button>
        </Can>
      ) : null}

      <ConfirmDialog
        open={confirming}
        onClose={() => {
          setConfirming(false);
          retry.reset();
        }}
        onConfirm={() => void retry.mutate(undefined)}
        tone="primary"
        title={`Retry the e-Way bill for ${invoiceNumber}?`}
        body="The same consignment is sent to the portal again now, instead of waiting for the countdown. It carries the same idempotency key as every earlier attempt, so it cannot produce a second bill."
        confirmLabel="Retry now"
        pendingLabel="Requesting…"
        pending={retry.pending}
        {...(retry.error
          ? {
              error: problemMessage(retry.error),
              ...(retry.error instanceof ApiError && retry.error.problem?.request_id
                ? { reference: retry.error.problem.request_id }
                : {}),
            }
          : {})}
      />
    </div>
  );
}
