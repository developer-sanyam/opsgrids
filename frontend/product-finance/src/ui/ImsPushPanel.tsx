import { useState } from "react";

import { requestImsPush } from "@/api/client";
import type { ImsPushSummary } from "@/api/types";
import { periodLabel } from "@/data/compliance";
import { ApiError, Can, problemMessage, useMutation } from "@opsgrids/client";
import { Button, Card, ConfirmDialog, SectionHeader } from "@opsgrids/ui";

interface ImsPushPanelProps {
  /** The return period the queue is filtered to, `YYYY-MM`; empty when it is not. */
  period: string;
  /** Null while it loads (or when no period is chosen). */
  summary: ImsPushSummary | null;
  /** Called after the server accepted the request, so the page can watch it happen. */
  onRequested: (requested: number) => void;
}

/** One count in the strip. A label and a number, never a colour standing in for either. */
function Count({ label, value, emphasis = false }: { label: string; value: number; emphasis?: boolean }) {
  return (
    <div className="flex flex-col gap-0.5">
      <dt className="text-xs font-semibold uppercase tracking-wide text-faint">{label}</dt>
      <dd className={emphasis && value > 0 ? "tnum text-lg font-extrabold text-ink" : "tnum text-lg font-semibold text-ink-soft"}>
        {value}
      </dd>
    </div>
  );
}

/**
 * Where one period's decisions stand with GSTN, and the one control that sends them.
 *
 * Recording a decision on this screen tells GSTN nothing — it is the platform's own position,
 * appended to its own ledger. Sending is a separate act behind a separate, dangerous permission
 * (`finance.compliance.ims.push`): a clerk who may record decisions for a supervisor to review must
 * not be able to send them by holding that alone. So the button is offered only to whoever holds
 * it (`<Can>` hides a control and protects nothing — the endpoint refuses on its own), only when
 * there is something to send, and never without saying what it will do first.
 *
 * It asks for a **period**, because that is the unit a push is made in and rate limited by; with
 * none chosen it says so instead of guessing which of several periods a person meant.
 */
export function ImsPushPanel({ period, summary, onRequested }: ImsPushPanelProps) {
  const [confirming, setConfirming] = useState(false);

  const push = useMutation(() => requestImsPush(period), {
    onSuccess: (result) => {
      setConfirming(false);
      onRequested(result.requested);
    },
  });

  const label = period === "" ? "" : periodLabel(period);
  const pushable = summary?.pushable ?? 0;
  const rateLimited = push.error instanceof ApiError && push.error.status === 429;

  return (
    <Card className="flex flex-col gap-4 p-4 sm:p-5">
      <SectionHeader
        title="Sending decisions to GSTN"
        subtitle="Recording a decision does not tell GSTN. Sending a period does, and needs its own permission."
      />

      {period === "" ? (
        <p className="text-sm text-muted">
          Choose a return period above to see where its decisions stand with GSTN and to send them.
          A period is the unit they are sent in.
        </p>
      ) : summary === null ? (
        <p className="text-sm text-muted">Reading where {label} stands…</p>
      ) : (
        <dl className="grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-3 lg:grid-cols-6">
          <Count label="Waiting to send" value={summary.queued} emphasis />
          <Count label="Sending" value={summary.submitting} />
          <Count label="On GSTN" value={summary.pushed} />
          <Count label="Send failed" value={summary.failed} emphasis />
          <Count label="Conflict" value={summary.conflict} emphasis />
          <Count label="GSTN differs" value={summary.drift} emphasis />
        </dl>
      )}

      {period !== "" && summary !== null ? (
        <div className="flex flex-col gap-2">
          <Can
            permission="finance.compliance.ims.push"
            scope="ORG"
            fallback={
              <p className="text-sm text-muted">
                You can see where {label} stands but not send it — sending is a separate permission
                (<span className="money">finance.compliance.ims.push</span>). Ask an administrator
                if you need it.
              </p>
            }
          >
            <div className="flex flex-wrap items-center gap-3">
              <Button
                variant="dark"
                size="md"
                disabled={pushable === 0}
                onClick={() => {
                  push.reset();
                  setConfirming(true);
                }}
              >
                {pushable === 0
                  ? "Nothing to send"
                  : `Send ${pushable} decision${pushable === 1 ? "" : "s"} to GSTN…`}
              </Button>
              {summary.requested > 0 || summary.submitting > 0 ? (
                <p role="status" className="text-sm font-medium text-info">
                  Sending — {summary.requested + summary.submitting} still under way. This page
                  updates as GSTN answers.
                </p>
              ) : null}
            </div>
          </Can>
          {summary.conflict > 0 ? (
            <p className="max-w-prose text-xs text-muted">
              A conflict means GSTN holds something else for that document — decide it again to send
              a newer decision. It is never sent over.
            </p>
          ) : null}
        </div>
      ) : null}

      <ConfirmDialog
        open={confirming}
        onClose={() => {
          setConfirming(false);
          push.reset();
        }}
        onConfirm={() => void push.mutate(undefined)}
        tone="primary"
        title={`Send ${pushable} decision${pushable === 1 ? "" : "s"} for ${label} to GSTN?`}
        body="GSTN will record each accept, reject and hold you decided. The credit on your own ledger already follows your decisions; this tells the portal. Decisions it refused earlier are sent again, and a decision sent twice is recorded once."
        confirmLabel="Send to GSTN"
        pendingLabel="Requesting…"
        pending={push.pending}
        {...(push.error
          ? {
              error: rateLimited
                ? "This period was asked for moments ago. Give it a few seconds, then try again."
                : problemMessage(push.error),
              ...(push.error instanceof ApiError && push.error.problem?.request_id
                ? { reference: push.error.problem.request_id }
                : {}),
            }
          : {})}
      />
    </Card>
  );
}
