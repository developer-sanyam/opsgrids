import { useCallback, useId, useMemo, useRef, useState } from "react";
import type { MouseEvent } from "react";
import { ConfirmDialog, FormDialog, InlineAlert, TextArea, TextField } from "@opsgrids/ui";

import { extendTrial } from "@/api/client";
import type { ExtendTrialResult, PricingOrgRef, ProductPricing } from "@/api/types";
import { useAuth } from "@/auth/context";
import { ENTITLEMENT_LAG_COPY, failureKind, notMadeGuidance, unknownOutcomeGuidance } from "@/lib/planChangeCopy";
import {
  EXTEND_ROLE_HINT,
  IST_LABEL,
  endOfIstDay,
  extendPreviewSentence,
  extendResultSentence,
  extensionWindow,
  formatIst,
  formatIstDate,
  totalTrialDays,
} from "@/lib/staffSubscriptionCopy";
import { Button, Card, DataTable, EmptyState, ErrorState, SectionHeader, Skeleton } from "@/ui";
import type { Column } from "@/ui";
import { useLivePricing } from "@/lib/useLivePricing";
import { failureOf } from "./staffActionFailure";
import type { Failure } from "./staffActionFailure";

const MAX_REASON = 500;

interface FlowProps {
  org: PricingOrgRef;
  product: ProductPricing;
  now: Date;
  onClose: () => void;
  onRefresh: () => void;
  onDone: (result: ExtendTrialResult) => void;
}

/**
 * Extend trial: pick the trial's new last day **in IST**, say why, confirm. The request carries the
 * absolute end (23:59:59 IST on that day) and the end the operator saw, exactly as it was read — so
 * a repeat can never add the time twice, and a trial that moved in between is refused rather than
 * stretched blind.
 */
function ExtendTrialFlow({ org, product, now, onClose, onRefresh, onDone }: FlowProps) {
  const sub = product.subscription;
  const range = sub ? extensionWindow(sub.trial_ends_at, sub.trial_max_ends_at, now) : null;
  const totalDays = sub ? totalTrialDays(sub.current_period_start, sub.trial_max_ends_at) : null;

  const [date, setDate] = useState("");
  const [reason, setReason] = useState("");
  const [touched, setTouched] = useState(false);
  const [phase, setPhase] = useState<"form" | "confirm">("form");
  const [formFailure, setFormFailure] = useState<Failure | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [confirmFailure, setConfirmFailure] = useState<Failure | null>(null);
  const keyRef = useRef<string | null>(null);
  const confirmingRef = useRef(false);

  const reasonTrimmed = reason.trim();
  const inWindow = range !== null && date >= range.min && date <= range.max;
  const dateError = !touched
    ? undefined
    : date === ""
      ? "Choose the trial's new last day."
      : !inWindow && range
        ? `Choose a day from ${formatIstDate(range.min)} to ${formatIstDate(range.max)}.`
        : undefined;
  const reasonError = !touched
    ? undefined
    : reasonTrimmed.length === 0
      ? "Enter a reason."
      : reasonTrimmed.length > MAX_REASON
        ? `Keep the reason to ${MAX_REASON} characters or fewer.`
        : undefined;

  function review() {
    setTouched(true);
    setFormFailure(null);
    if (!inWindow || reasonTrimmed.length === 0 || reasonTrimmed.length > MAX_REASON) return;
    keyRef.current = crypto.randomUUID();
    setConfirmFailure(null);
    setPhase("confirm");
  }

  async function confirm() {
    if (confirmingRef.current || !sub?.trial_ends_at || keyRef.current === null) return;
    confirmingRef.current = true;
    setConfirming(true);
    setConfirmFailure(null);
    try {
      const result = await extendTrial(
        org.organization_id,
        product.product_key,
        // The end the operator saw goes back exactly as it was read: the server compares it to the
        // microsecond under its lock.
        { new_trial_ends_at: endOfIstDay(date), expected_trial_ends_at: sub.trial_ends_at, reason: reasonTrimmed },
        keyRef.current,
      );
      onDone(result);
    } catch (err) {
      const { failure, status, code } = failureOf(err);
      const kind = failureKind(status, code);
      if (kind === "refused") {
        keyRef.current = null;
        setPhase("form");
        setFormFailure(failure);
        onRefresh();
      } else {
        setConfirmFailure({
          ...failure,
          guidance:
            kind === "unknown"
              ? unknownOutcomeGuidance()
              : kind === "not_made"
                ? notMadeGuidance(code)
                : failure.guidance,
        });
        if (kind === "unknown") onRefresh();
      }
    } finally {
      confirmingRef.current = false;
      setConfirming(false);
    }
  }

  return (
    <>
      <FormDialog
        open
        onClose={onClose}
        onSubmit={review}
        title={`Extend trial — ${product.product_name}`}
        description={`${org.name}. Dates are in ${IST_LABEL}.`}
        submitLabel="Review"
        {...(formFailure ? { error: formFailure.message } : {})}
        {...(formFailure?.reference ? { reference: formFailure.reference } : {})}
        dirty={date !== "" || reasonTrimmed.length > 0}
      >
        <p className="text-sm text-muted">
          The trial now ends <span className="font-semibold text-ink">{formatIst(sub?.trial_ends_at ?? null)}</span>.
          {totalDays !== null && sub?.trial_max_ends_at ? (
            <>
              {" "}
              A trial may run at most {totalDays} days in total, so this one can end no later than{" "}
              <span className="font-semibold text-ink">{formatIst(sub.trial_max_ends_at)}</span>.
            </>
          ) : null}
        </p>
        {formFailure?.guidance ? <p className="text-sm text-muted">{formFailure.guidance}</p> : null}
        <TextField
          label="New last day of the trial"
          type="date"
          required
          value={date}
          onChange={(e) => setDate(e.target.value)}
          {...(range ? { min: range.min, max: range.max } : {})}
          helper={`The trial ends at 23:59 ${IST_LABEL} on this day.`}
          {...(dateError ? { error: dateError } : {})}
        />
        <TextArea
          label="Reason"
          required
          rows={3}
          maxLength={MAX_REASON}
          counterFrom={400}
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          onBlur={() => setTouched(true)}
          helper="Recorded on the audit trail and the subscription's event. Not shown to the customer."
          {...(reasonError ? { error: reasonError } : {})}
        />
      </FormDialog>

      <ConfirmDialog
        open={phase === "confirm"}
        onClose={() => setPhase("form")}
        onConfirm={() => void confirm()}
        tone="primary"
        title="Confirm trial extension"
        body={
          date
            ? extendPreviewSentence(org.name, product.product_name, sub?.trial_ends_at ?? null, endOfIstDay(date))
            : ""
        }
        confirmLabel="Extend trial"
        pendingLabel="Extending…"
        cancelLabel="Back"
        pending={confirming}
        {...(confirmFailure ? { error: confirmFailure.message } : {})}
        {...(confirmFailure?.reference ? { reference: confirmFailure.reference } : {})}
      >
        <div className="flex flex-col gap-3 text-sm">
          <ul className="flex flex-col gap-2 text-muted">
            <li>
              Reason: <span className="text-ink">{reasonTrimmed}</span>
            </li>
          </ul>
          <p className="text-xs text-faint">{ENTITLEMENT_LAG_COPY}</p>
          {confirmFailure?.guidance ? <p className="text-sm text-muted">{confirmFailure.guidance}</p> : null}
        </div>
      </ConfirmDialog>
    </>
  );
}

interface DoneView {
  sentence: string;
}

/**
 * Extend trial — move a trialling subscription's end later (ADR-0030). **Owner only**: the button
 * is disabled with a hint for everyone else, and the server refuses them regardless.
 *
 * Driven by the live pricing composite (kept on screen while it reloads, like Change plan's), which
 * carries the trial's start and the cap the subscription service states. Its own card, hosting its
 * own dialogs. `refreshSignal` lets a sibling (Add product) ask it to re-read.
 */
export function ExtendTrialCard({
  org,
  refreshSignal = 0,
  now = new Date(),
}: {
  org: PricingOrgRef;
  refreshSignal?: number;
  /** The clock the window is computed against — tests pin it. */
  now?: Date;
}) {
  const { hasRole } = useAuth();
  const isOwner = hasRole("owner");
  const [reloadKey, setReloadKey] = useState(0);
  const [flow, setFlow] = useState<ProductPricing | null>(null);
  const [done, setDone] = useState<DoneView | null>(null);
  const trigger = useRef<HTMLElement | null>(null);
  const hintId = useId();

  const live = useLivePricing(org.organization_id, reloadKey + refreshSignal);
  const refresh = useCallback(() => setReloadKey((k) => k + 1), []);

  const trials = useMemo(() => {
    if (live.status !== "ready") return [];
    return live.data.products
      .filter((p) => p.subscription?.state === "trial")
      .sort((a, b) => a.product_name.localeCompare(b.product_name));
  }, [live]);

  const closeFlow = useCallback(() => {
    setFlow(null);
    window.requestAnimationFrame(() => trigger.current?.focus());
  }, []);

  const handleDone = useCallback(
    (result: ExtendTrialResult) => {
      const name = trials.find((p) => p.product_key === result.product_key)?.product_name ?? result.product_key;
      setDone({ sentence: extendResultSentence(org.name, name, result) });
      refresh();
      closeFlow();
    },
    [closeFlow, org.name, refresh, trials],
  );

  function open(product: ProductPricing, event: MouseEvent<HTMLButtonElement>) {
    trigger.current = event.currentTarget;
    setDone(null);
    setFlow(product);
  }

  const columns: Column<ProductPricing>[] = [
    {
      key: "product",
      header: "Product",
      render: (p) => (
        <div className="flex flex-col leading-tight">
          <span className="font-semibold text-ink">{p.product_name}</span>
          <span className="mono text-xs text-faint">{p.product_key}</span>
        </div>
      ),
    },
    {
      key: "ends",
      header: "Trial ends (IST)",
      render: (p) => <span className="tabular-nums">{formatIst(p.subscription?.trial_ends_at ?? null)}</span>,
    },
    {
      key: "cap",
      header: "Latest it can end (IST)",
      cellClassName: "hidden md:table-cell",
      headerClassName: "hidden md:table-cell",
      render: (p) => <span className="tabular-nums">{formatIst(p.subscription?.trial_max_ends_at ?? null)}</span>,
    },
    {
      key: "actions",
      header: "",
      align: "right",
      render: (p) => {
        const sub = p.subscription;
        const range = sub ? extensionWindow(sub.trial_ends_at, sub.trial_max_ends_at, now) : null;
        const enabled = isOwner && range !== null;
        return (
          <div className="flex flex-col items-end gap-1">
            <Button
              size="sm"
              variant="outline"
              disabled={!enabled}
              aria-describedby={enabled ? undefined : hintId}
              aria-label={`Extend trial for ${p.product_name}`}
              onClick={(event) => open(p, event)}
            >
              Extend trial
            </Button>
            {!isOwner ? (
              <span className="text-xs text-faint">{EXTEND_ROLE_HINT}</span>
            ) : range === null ? (
              <span className="text-xs text-faint">This trial is already at its longest.</span>
            ) : null}
          </div>
        );
      },
    },
  ];

  return (
    <Card id="extend-trial" className="flex scroll-mt-6 flex-col gap-4 p-6">
      <SectionHeader
        title="Extend trial"
        subtitle={`Move a trial's end later, up to its cap. Dates are in ${IST_LABEL}.`}
      />
      <p id={hintId} className="sr-only">
        {isOwner ? "This trial is already at its longest." : EXTEND_ROLE_HINT}
      </p>

      <div role="status" aria-live="polite" aria-atomic="true">
        {done ? (
          <InlineAlert
            tone="success"
            title="Trial extended"
            live="off"
            action={
              <Button size="sm" variant="outline" onClick={() => setDone(null)}>
                Dismiss
              </Button>
            }
          >
            {done.sentence} {ENTITLEMENT_LAG_COPY}
          </InlineAlert>
        ) : null}
      </div>

      {live.status === "loading" ? (
        <div className="flex flex-col gap-2">
          <Skeleton className="h-12 w-full" />
        </div>
      ) : live.status === "error" ? (
        <ErrorState message={live.error.message} onRetry={refresh} />
      ) : trials.length === 0 ? (
        <EmptyState title="No trials to extend" description="None of this organization's subscriptions is trialling." />
      ) : (
        <DataTable columns={columns} rows={trials} rowKey={(p) => p.product_key} caption="Subscriptions on a trial" />
      )}

      {flow ? (
        <ExtendTrialFlow
          org={org}
          product={flow}
          now={now}
          onClose={closeFlow}
          onRefresh={refresh}
          onDone={handleDone}
        />
      ) : null}
    </Card>
  );
}
