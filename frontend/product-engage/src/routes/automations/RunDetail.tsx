import { useCallback, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Link, useParams } from "react-router-dom";

import { useAsync } from "@opsgrids/client";
import { ArrowLeftIcon } from "@opsgrids/ui";

import { getRun, retryRun } from "@/api/automations";
import type { RunDetailOut, RunStepOut } from "@/api/automations";
import { failureMessage } from "@/lib/errors";
import { formatInstant } from "@/lib/format";
import { useFocusHeading } from "@/lib/useFocusHeading";
import { Announcer, Button, Card, ConfirmDialog, EmptyState, InlineAlert, PageHeading, Skeleton, useAnnounce } from "@/ui";
import { NODE_LABEL } from "./graph";
import { stepStateLabel } from "./labels";
import { RunStateTag, StepStateTag } from "./Tags";
import { LoadError } from "./LoadError";

/**
 * One run: what started it, where it is, and every step it took, in order.
 *
 * A step that failed shows the service's own detail sentence and how many attempts it took; a
 * step that is waiting says when the run wakes. A failed run can be retried once someone has looked
 * at why — the service refuses a retry of anything else, and this screen only offers it for a
 * failed run. Retrying asks first, because it sends the failed step again.
 */

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-1">
      <dt className="text-xs font-bold uppercase tracking-wider text-faint">{label}</dt>
      <dd className="text-sm text-ink">{children}</dd>
    </div>
  );
}

function StepRow({ step, number }: { step: RunStepOut; number: number }) {
  return (
    <li className="flex flex-col gap-1 border-t border-line py-3 first:border-t-0">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-bold text-ink">
          {number}. {NODE_LABEL[step.type]}
        </span>
        <StepStateTag state={step.state} />
        {step.attempt > 1 ? <span className="text-xs text-muted">Attempt {step.attempt}</span> : null}
      </div>
      <p className="text-xs text-muted">
        <span className="sr-only">Step {number} </span>
        {step.started_at === null ? "Not started" : `Started ${formatInstant(step.started_at)}`}
        {step.finished_at !== null ? `, finished ${formatInstant(step.finished_at)}` : ""}
        <span className="sr-only">. Status: {stepStateLabel(step.state)}</span>
      </p>
      {step.detail !== null ? (
        <p className={`text-sm ${step.state === "failed" ? "text-negative" : "text-ink"}`}>{step.detail}</p>
      ) : null}
      {step.message_id !== null ? <p className="text-xs text-muted">Message {step.message_id}</p> : null}
    </li>
  );
}

function RunView({ run, automationId, reload }: { run: RunDetailOut; automationId: string; reload: () => void }) {
  const announce = useAnnounce();
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const titleRef = useRef<HTMLHeadingElement>(null);
  const focusHeading = useFocusHeading(titleRef);

  async function retry() {
    if (busy) return;
    setBusy(true);
    setFailure(null);
    try {
      await retryRun(run.id);
      setConfirming(false);
      announce("Retry requested. The run will pick up from the failed step.");
      focusHeading();
      reload();
    } catch (caught) {
      setFailure(failureMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="rise flex flex-col gap-6">
      <Link
        to={`/automations/${automationId}`}
        className="relative hit-44 inline-flex w-fit items-center gap-2 text-sm font-semibold text-muted transition-colors hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
      >
        <ArrowLeftIcon className="size-4" />
        Back to automation
      </Link>
      <PageHeading
        ref={titleRef}
        eyebrow="Automation run"
        title={`Run for ${run.subject}`}
        description={`Started ${formatInstant(run.started_at)} on version ${run.version}.`}
        actions={
          <>
            <RunStateTag state={run.state} />
            {run.state === "failed" ? <Button onClick={() => setConfirming(true)}>Retry run</Button> : null}
          </>
        }
      />

      {run.state === "failed" ? (
        <InlineAlert tone="danger" title="This run failed">
          {run.failure_code !== null ? `Reason: ${run.failure_code}. ` : ""}
          Look at the failed step below, fix what it names, then retry.
        </InlineAlert>
      ) : null}

      <Card className="p-6">
        <dl className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Fact label="State">
            <RunStateTag state={run.state} />
          </Fact>
          <Fact label="Finished">{run.finished_at === null ? "Not finished" : formatInstant(run.finished_at)}</Fact>
          <Fact label="Next wake-up">{run.next_wake_at === null ? "—" : formatInstant(run.next_wake_at)}</Fact>
          <Fact label="Source event">{run.source_event_id}</Fact>
          <Fact label="Idempotency key">{run.idempotency_key}</Fact>
        </dl>
      </Card>

      <Card className="flex flex-col gap-2 p-6">
        <h2 className="text-lg font-bold">Steps</h2>
        {run.steps.length === 0 ? (
          <EmptyState
            title="No steps yet"
            description="This run hasn't started its first step. Steps appear here as it moves through the flow."
          />
        ) : (
          <ol aria-label="Steps this run took">
            {run.steps.map((step, index) => (
              <StepRow key={`${step.node_id}-${index}`} step={step} number={index + 1} />
            ))}
          </ol>
        )}
      </Card>

      <ConfirmDialog
        open={confirming}
        onClose={() => {
          if (!busy) {
            setConfirming(false);
            setFailure(null);
          }
        }}
        onConfirm={() => void retry()}
        title="Retry this run?"
        body="The failed step is attempted again. If it sends a message, the message goes out again."
        confirmLabel="Retry run"
        pendingLabel="Retrying…"
        tone="primary"
        pending={busy}
        {...(failure !== null ? { error: failure } : {})}
      />
    </div>
  );
}

function RunLoader({ automationId, runId }: { automationId: string; runId: string }) {
  const loader = useCallback(() => getRun(runId), [runId]);
  const state = useAsync(loader, [runId]);

  if (state.status === "loading") {
    return (
      <Card className="flex flex-col gap-4 p-6" aria-busy="true">
        <span className="sr-only" role="status">
          Loading run
        </span>
        <Skeleton className="h-6 w-48" />
        <Skeleton className="h-48 w-full rounded-tile" />
      </Card>
    );
  }
  if (state.status === "error") {
    return <LoadError error={state.error} title="Couldn't load this run" onRetry={state.reload} />;
  }
  return <RunView run={state.data} automationId={automationId} reload={state.reload} />;
}

export function RunDetailPage() {
  const { automationId, runId } = useParams<{ automationId: string; runId: string }>();
  if (automationId === undefined || runId === undefined) return null;
  return (
    <Announcer>
      <RunLoader automationId={automationId} runId={runId} />
    </Announcer>
  );
}
