import { useState } from "react";
import type { Ref } from "react";

import { useMutation } from "@opsgrids/client";
import { ArrowRightIcon, cn } from "@opsgrids/ui";

import { listWhatsAppNumbers, setDefaultNumber } from "@/api/whatsappSignup";
import type { WhatsAppNumber } from "@/api/whatsappSignup";
import { failureMessage } from "@/lib/errors";
import { Button, EmptyState, InlineAlert, Skeleton, StatusBadge } from "@/ui";
import {
  displayNameLabel,
  isSelectable,
  limitLabel,
  maskedId,
  numberStatus,
  qualityLabel,
} from "./labels";
import { LoadError, StepCard } from "./parts";
import { usePollUntil } from "./usePollUntil";
import { useWabaId } from "./useWabaId";

const NUMBERS_POLL_MS = 2000;
const NUMBERS_GIVE_UP_MS = 120_000;

function NumberCard({
  number,
  checked,
  onSelect,
}: {
  number: WhatsAppNumber;
  checked: boolean;
  onSelect: () => void;
}) {
  const selectable = isSelectable(number);
  const status = numberStatus(number, "select");
  return (
    <label
      className={cn(
        "flex items-start gap-3 rounded-card p-4",
        selectable ? "cursor-pointer" : "cursor-not-allowed bg-ground",
        checked ? "bg-accent-soft shadow-card" : "border border-line",
      )}
    >
      <input
        type="radio"
        name="number"
        value={number.id}
        checked={checked}
        disabled={!selectable}
        onChange={onSelect}
        className="mt-1 size-5 shrink-0 accent-accent"
      />
      <span className="flex min-w-0 flex-1 flex-col gap-3">
        <span className="flex flex-wrap items-center justify-between gap-2">
          <span className="text-section font-bold text-ink tnum">{number.display_phone_number}</span>
          <StatusBadge tone={status.tone} label={status.label} />
        </span>
        <span className="grid grid-cols-1 gap-3 text-body-sm text-ink-soft sm:grid-cols-3">
          <span>
            <span className="block text-caption text-muted">Display name</span>
            {displayNameLabel(number)}
          </span>
          <span>
            <span className="block text-caption text-muted">Quality</span>
            {qualityLabel(number.quality_rating)}
          </span>
          <span>
            <span className="block text-caption text-muted">Messaging limit</span>
            {limitLabel(number.messaging_limit)}
          </span>
        </span>
      </span>
    </label>
  );
}

function NumbersSkeleton() {
  return (
    <div className="flex flex-col gap-3" aria-hidden>
      <Skeleton className="h-24 w-full rounded-card" />
      <Skeleton className="h-24 w-full rounded-card" />
    </div>
  );
}

/**
 * Step 2: which of the numbers Meta shared to send from.
 *
 * The numbers arrive from the setup job, which stores the token before it can list them — so an
 * empty answer straight after Meta closes is expected, not a failure. The list is asked for again
 * every couple of seconds until a number is there, for at most two minutes; after that the screen
 * says so and points at the setup status instead of spinning for ever.
 */
export function SelectStep({
  onBack,
  onChosen,
  onCheckSetup,
  headingRef,
}: {
  onBack: () => void;
  onChosen: () => void;
  onCheckSetup: () => void;
  headingRef: Ref<HTMLHeadingElement>;
}) {
  const numbers = usePollUntil({
    loader: listWhatsAppNumbers,
    isFinal: (data) => data.items.length > 0,
    intervalMs: NUMBERS_POLL_MS,
    maxMs: NUMBERS_GIVE_UP_MS,
  });
  const wabaId = useWabaId();

  const [picked, setPicked] = useState<string | null>(null);
  const choose = useMutation((id: string) => setDefaultNumber(id), { onSuccess: onChosen });

  const items = numbers.data?.items ?? [];
  const fallback = items.find((n) => n.is_default && isSelectable(n)) ?? items.find(isSelectable) ?? null;
  const selectedId = picked ?? fallback?.id ?? null;

  return (
    <StepCard
      title="Choose the number to use"
      description="These are the numbers Meta shared with OpsGrids. You can add more later in Settings."
      headingRef={headingRef}
      size="lg"
    >
      {wabaId !== null ? (
        <div className="flex flex-col gap-1 rounded-tile bg-ground px-4 py-3">
          <span className="text-caption font-semibold text-muted">WhatsApp Business Account</span>
          <span className="text-body font-bold text-ink tnum">{maskedId(wabaId)}</span>
        </div>
      ) : null}

      {numbers.status === "loading" ? <NumbersSkeleton /> : null}

      {numbers.status === "error" ? (
        <LoadError error={numbers.error} title="Couldn't load your phone numbers" onRetry={numbers.retry} />
      ) : null}

      {numbers.status === "polling" || numbers.status === "timedOut" || numbers.status === "stale" ? (
        items.length === 0 ? (
          numbers.status === "timedOut" ? (
            <EmptyState
              title="Meta hasn't shared a number yet"
              description="Setup may still be running, or it may have stopped. Check its status to see which."
              action={<Button variant="outline" onClick={onCheckSetup}>Check setup status</Button>}
            />
          ) : (
            <div role="status" aria-live="polite" className="flex flex-col gap-3">
              <p className="text-body-sm text-muted">Waiting for Meta to share your numbers…</p>
              <NumbersSkeleton />
            </div>
          )
        ) : null
      ) : null}

      {numbers.status === "stale" ? (
        <InlineAlert tone="warning" title="Couldn't refresh your numbers">
          {numbers.error.message} What is shown may be out of date.
        </InlineAlert>
      ) : null}

      {items.length > 0 ? (
        <fieldset className="m-0 flex flex-col gap-3 border-0 p-0">
          <legend className="sr-only">Phone number</legend>
          {items.map((number) => (
            <NumberCard
              key={number.id}
              number={number}
              checked={selectedId === number.id}
              onSelect={() => {
                setPicked(number.id);
                choose.reset();
              }}
            />
          ))}
        </fieldset>
      ) : null}

      <p className="flex items-start gap-3 rounded-tile bg-info-soft px-4 py-3 text-body-sm text-ink">
        The messaging limit is how many customers you can start conversations with each day. It rises as
        your number sends good-quality messages.
      </p>

      <div aria-live="polite" className="empty:hidden">
        {choose.error !== null ? (
          <InlineAlert tone="danger" title="That number wasn't chosen">
            {failureMessage(choose.error)}
          </InlineAlert>
        ) : null}
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3">
        <Button variant="outline" onClick={onBack} disabled={choose.pending}>
          Back
        </Button>
        <Button
          onClick={() => {
            if (selectedId !== null) void choose.mutate(selectedId);
          }}
          disabled={selectedId === null}
          loading={choose.pending}
          trailingIcon={<ArrowRightIcon className="size-4" aria-hidden />}
        >
          {choose.pending ? "Saving…" : "Use this number"}
        </Button>
      </div>
    </StepCard>
  );
}
