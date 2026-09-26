import { useEffect, useRef } from "react";
import type { Ref } from "react";

import { ArrowRightIcon, CheckIcon, cn } from "@opsgrids/ui";

import { getSignupStatus } from "@/api/whatsappSignup";
import type { SignupStatusOut, SignupStep } from "@/api/whatsappSignup";
import { Button, InlineAlert, Skeleton, useAnnounce } from "@/ui";
import { STEP_HINT, STEP_STATE_LABEL } from "./labels";
import { LoadError, StepCard } from "./parts";
import { usePollUntil } from "./usePollUntil";

const STATUS_POLL_MS = 1500;
const STATUS_GIVE_UP_MS = 180_000;

function StepMarker({ state }: { state: SignupStep["state"] }) {
  if (state === "done") {
    return (
      <span aria-hidden className="grid size-7 shrink-0 place-items-center rounded-full bg-positive text-on-accent">
        <CheckIcon className="size-4" strokeWidth={2.6} />
      </span>
    );
  }
  if (state === "running") {
    return (
      <svg aria-hidden viewBox="0 0 28 28" fill="none" className="spin size-7 shrink-0">
        <circle cx="14" cy="14" r="11" stroke="currentColor" strokeWidth="3" className="text-line" />
        <path d="M14 3a11 11 0 0 1 11 11" stroke="currentColor" strokeWidth="3" strokeLinecap="round" className="text-accent" />
      </svg>
    );
  }
  if (state === "failed") {
    return (
      <span aria-hidden className="grid size-7 shrink-0 place-items-center rounded-full bg-negative text-on-negative text-body-sm font-bold">
        !
      </span>
    );
  }
  return <span aria-hidden className="size-7 shrink-0 rounded-full border-2 border-field-edge" />;
}

function StepRow({ step }: { step: SignupStep }) {
  return (
    <li
      className={cn(
        "flex items-center gap-3 border-t border-line p-4 first:border-t-0",
        step.state === "running" && "bg-accent-soft",
      )}
    >
      <StepMarker state={step.state} />
      <span className="flex min-w-0 flex-1 flex-col gap-1">
        <span className="text-body font-bold text-ink">{step.label}</span>
        <span className="text-body-sm text-muted">{step.detail ?? STEP_HINT[step.key]}</span>
      </span>
      <span
        className={cn(
          "shrink-0 text-body-sm font-semibold",
          step.state === "done" && "text-positive",
          step.state === "running" && "text-accent-ink",
          step.state === "failed" && "text-negative",
          step.state === "waiting" && "text-muted",
        )}
      >
        {STEP_STATE_LABEL[step.state]}
      </span>
    </li>
  );
}

function StepsSkeleton() {
  return (
    <div className="flex flex-col gap-3" aria-hidden>
      <Skeleton className="h-4 w-full" />
      <Skeleton className="h-64 w-full rounded-card" />
    </div>
  );
}

/** Which of Meta's setup steps the server names as the reason it stopped. */
function failedStep(status: SignupStatusOut): SignupStep | null {
  return status.steps.find((step) => step.state === "failed") ?? null;
}

/**
 * Step 3: the setup job, read back every second and a half.
 *
 * The list, its labels and its detail lines are the server's own — this screen adds a progress bar
 * and a status word per step (never colour alone), and says when a read could not be completed
 * ("stale") rather than showing an old answer as if it were current. A `failed` job is final:
 * the code Meta issued is single-use, so trying again means starting from the Meta window.
 */
export function SetupStep({
  setupId,
  onBack,
  onRestart,
  onDone,
  headingRef,
}: {
  setupId: string;
  onBack: () => void;
  onRestart: () => void;
  onDone: () => void;
  headingRef: Ref<HTMLHeadingElement>;
}) {
  const announce = useAnnounce();
  const poll = usePollUntil({
    loader: () => getSignupStatus(setupId),
    isFinal: (status) => status.status !== "running",
    intervalMs: STATUS_POLL_MS,
    maxMs: STATUS_GIVE_UP_MS,
  });

  const status = poll.data;
  const announced = useRef<string | null>(null);
  useEffect(() => {
    if (status === null || status.status === "running" || announced.current === status.status) return;
    announced.current = status.status;
    announce(status.status === "done" ? "Setup finished. WhatsApp is connected." : "Setup didn't finish.");
  }, [status, announce]);

  const total = status?.steps.length ?? 0;
  const finished = status?.steps.filter((step) => step.state === "done").length ?? 0;
  const percent = total === 0 ? 0 : Math.round((finished / total) * 100);
  const failed = status === null ? null : failedStep(status);

  return (
    <StepCard
      title="Setting things up"
      description="OpsGrids is finishing the connection with Meta. This usually takes under a minute."
      headingRef={headingRef}
      size="lg"
    >
      {poll.status === "loading" ? <StepsSkeleton /> : null}

      {poll.status === "error" ? (
        <LoadError error={poll.error} title="Couldn't read the setup status" onRetry={poll.retry} />
      ) : null}

      {status !== null ? (
        <>
          <div className="flex flex-col gap-2">
            <div className="flex items-center justify-between text-body-sm font-semibold text-ink">
              <span>
                {finished} of {total} steps done
              </span>
              <span className="text-accent-ink tnum">{percent}%</span>
            </div>
            <div
              role="progressbar"
              aria-label="Setup progress"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={percent}
              className="h-2 overflow-hidden rounded-full bg-sunken"
            >
              <div
                className="h-2 rounded-full bg-accent"
                style={{ width: `${percent}%` }}
              />
            </div>
          </div>

          <ul className="m-0 flex list-none flex-col overflow-hidden rounded-card p-0 shadow-card">
            {status.steps.map((step) => (
              <StepRow key={step.key} step={step} />
            ))}
          </ul>
        </>
      ) : null}

      <div role="status" aria-live="polite" className="flex flex-col gap-3 empty:hidden">
        {poll.status === "stale" ? (
          <InlineAlert tone="warning" live="off" title="Couldn't refresh the setup status">
            {poll.error.message} What is shown may be out of date. Setup keeps running on the server.
            <span className="mt-2 block">
              <Button variant="outline" size="sm" onClick={poll.retry}>
                Check again
              </Button>
            </span>
          </InlineAlert>
        ) : null}
        {poll.status === "timedOut" ? (
          <InlineAlert tone="warning" live="off" title="This is taking longer than usual">
            Setup is still running on the server. You can leave this page and check Settings later.
            <span className="mt-2 block">
              <Button variant="outline" size="sm" onClick={poll.retry}>
                Check again
              </Button>
            </span>
          </InlineAlert>
        ) : null}
        {status?.status === "failed" ? (
          <InlineAlert tone="danger" live="off" title="Setup didn't finish">
            {failed?.detail ?? "Meta or the server refused a step."}{" "}
            {status.error_code !== null ? `Reason: ${status.error_code}. ` : ""}
            Your organization is not connected. Start again from the Meta window.
          </InlineAlert>
        ) : null}
        {status?.status === "done" ? (
          <InlineAlert tone="success" live="off" title="Setup finished. WhatsApp is connected." />
        ) : null}
      </div>

      {status?.status !== "failed" ? (
        <InlineAlert tone="info" live="off" title="You can leave this page">
          Setup keeps running, and the connection shows in Settings when it finishes.
        </InlineAlert>
      ) : null}

      <div className="flex flex-wrap items-center justify-between gap-3">
        <Button variant="ghost" onClick={onBack}>
          Back
        </Button>
        {status?.status === "failed" ? (
          <Button onClick={onRestart}>Start again</Button>
        ) : (
          <Button
            onClick={onDone}
            disabled={status?.status !== "done"}
            trailingIcon={<ArrowRightIcon className="size-4" aria-hidden />}
          >
            Continue
          </Button>
        )}
      </div>
    </StepCard>
  );
}
