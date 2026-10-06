import { useId, useMemo, useState } from "react";

import { ApiError, problemMessage, useAsync } from "@opsgrids/client";
import { Button, ConfirmDialog, FormDialog, InlineAlert, RadioGroup, Skeleton, Tag } from "@opsgrids/ui";
import type { RadioOption } from "@opsgrids/ui";

import { changeSubscriptionPlan, getSubscriptionPlanOptions, previewSubscriptionPlanChange } from "@/api";
import type { PlanChangePreview, PlanOptions, Subscription } from "@/api/types";
import {
  confirmLabel,
  confirmTitle,
  optionDescription,
  optionLabels,
  planName,
  previewNotes,
  previewSentence,
  priceLine,
  refusalGuidance,
  uniqueOptions,
} from "@/lib/subscriptionCopy";
import { formatDate } from "@/lib/money";
import { problemCode } from "@/lib/problemCode";

/** A refusal or failure, as the dialog shows it: the server's sentence and what to do next. */
interface Failure {
  message: string;
  guidance: string | null;
  reference: string | undefined;
}

function failureOf(error: unknown): Failure {
  const code = problemCode(error);
  return {
    message: problemMessage(error),
    guidance: refusalGuidance(code),
    reference: error instanceof ApiError ? error.problem?.request_id : undefined,
  };
}

/** Codes that mean "look again": the confirm returns to the picker with fresh options. */
const BACK_TO_PICKER = new Set(["state_changed", "not_offerable", "already_pinned", "state"]);

interface Props {
  subscription: Subscription;
  /** The product's display name: "Revenue". */
  product: string;
  /** Product keys to names, for "Adds access to …". */
  productNames: (keys: string[]) => string[];
  onClose: () => void;
  /** The change landed; the sentence says what happened, for the page's announcement. */
  onChanged: (message: string) => void;
}

/**
 * Change plan, in two steps, mounted only while open — closing resets everything structurally.
 *
 * 1. **Pick** (`FormDialog`): the plans on offer to this organisation now, from the server — each
 *    once, the plan in force marked and disabled, a plan this subscription cannot take listed with
 *    the reason. Price only where the server sent one (the organisation's own negotiated price).
 * 2. **Confirm** (`ConfirmDialog`, stacked so *Back* keeps the choice): the server's own preview,
 *    in a sentence — now or at the period end, charged or not. Nothing here says "immediately"
 *    unless the preview did. The confirm carries the preview's `expected` state, so a subscription
 *    that moved in between is refused (`state_changed`) and the picker is shown again.
 */
export function ChangePlanDialog({ subscription, product, productNames, onClose, onChanged }: Props) {
  const groupName = useId();
  const options = useAsync(() => getSubscriptionPlanOptions(subscription.id), [subscription.id]);
  const [selected, setSelected] = useState<string | null>(null);
  const [pickError, setPickError] = useState<string | undefined>(undefined);
  const [formFailure, setFormFailure] = useState<Failure | null>(null);
  const [checking, setChecking] = useState(false);
  const [preview, setPreview] = useState<PlanChangePreview | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [confirmFailure, setConfirmFailure] = useState<Failure | null>(null);

  const data: PlanOptions | null = options.status === "success" ? options.data : null;
  const choices = useMemo(() => (data ? uniqueOptions(data.options) : []), [data]);
  const labels = useMemo(() => optionLabels(choices), [choices]);
  const target = choices.find((o) => o.plan_version_id === selected) ?? null;
  const selectable = choices.filter((o) => o.blocked_code === null);

  const radioOptions: RadioOption[] = choices.map((o) => ({
    value: o.plan_version_id,
    label: labels.get(o.plan_version_id) ?? planName(o),
    description: optionDescription(o),
    disabled: o.blocked_code !== null,
    aside: o.is_current ? (
      <Tag tone="info">Current plan</Tag>
    ) : o.is_pending ? (
      <Tag tone="warning">Scheduled</Tag>
    ) : o.negotiated ? (
      <Tag>Negotiated</Tag>
    ) : undefined,
  }));

  async function review() {
    setPickError(undefined);
    setFormFailure(null);
    if (selected === null) {
      setPickError("Choose the plan to move to.");
      return;
    }
    if (checking) return;
    setChecking(true);
    try {
      const answer = await previewSubscriptionPlanChange(subscription.id, selected);
      if (answer.refusal_code !== null) {
        // The refusal is the answer; the confirm step is never offered for it.
        setFormFailure({
          message: answer.refusal_detail ?? "This change can't be made.",
          guidance: refusalGuidance(answer.refusal_code),
          reference: undefined,
        });
        if (answer.refusal_code === "not_offerable") {
          setSelected(null);
          options.reload();
        }
        return;
      }
      setConfirmFailure(null);
      setPreview(answer);
    } catch (error) {
      // A preview is a read: when it fails, nothing was changed.
      setFormFailure({ ...failureOf(error), guidance: "Nothing was changed. Try again in a moment." });
    } finally {
      setChecking(false);
    }
  }

  async function confirm() {
    if (confirming || preview === null || selected === null) return;
    setConfirming(true);
    setConfirmFailure(null);
    try {
      await changeSubscriptionPlan(subscription.id, selected, preview.expected);
      const toPlan = target ? planName(target) : "the new plan";
      onChanged(
        preview.effective === "next_period"
          ? `${product} moves to ${toPlan} on ${formatDate(preview.effective_at)}.`
          : `${product} is now on ${toPlan}.`,
      );
      onClose();
    } catch (error) {
      const code = problemCode(error);
      if (code !== null && BACK_TO_PICKER.has(code)) {
        // Repeating a refusal cannot change it: back to the picker, with the live options.
        setPreview(null);
        setFormFailure(failureOf(error));
        if (code === "not_offerable") setSelected(null);
        options.reload();
      } else {
        setConfirmFailure(failureOf(error));
      }
    } finally {
      setConfirming(false);
    }
  }

  const current = data?.current ?? null;
  const pending = data?.pending ?? null;
  const fromPlan = planName(current);
  const toPlan = target ? (labels.get(target.plan_version_id) ?? planName(target)) : "the new plan";
  const pendingPlan = pending ? planName(pending) : null;
  const blockedCode = data?.change_blocked_code ?? null;

  return (
    <>
      <FormDialog
        open
        onClose={onClose}
        onSubmit={() => void review()}
        title={`Change ${product}'s plan`}
        description="Choose a plan. Before anything changes you will see when it takes effect and whether anything is charged."
        submitLabel="Review change"
        pendingLabel="Checking…"
        pending={checking}
        {...(formFailure ? { error: formFailure.message } : {})}
        {...(formFailure?.reference ? { reference: formFailure.reference } : {})}
        dirty={selected !== null}
      >
        {options.status === "loading" ? (
          <div className="flex flex-col gap-3" aria-busy="true">
            <Skeleton className="h-16 w-full" />
            <Skeleton className="h-16 w-full" />
          </div>
        ) : options.status === "error" ? (
          <InlineAlert
            tone="danger"
            title="Couldn't load the plans on offer"
            action={
              <Button size="sm" variant="outline" onClick={options.reload}>
                Try again
              </Button>
            }
          >
            {problemMessage(options.error)}
          </InlineAlert>
        ) : (
          <>
            <div className="flex flex-col gap-1 text-sm text-muted">
              <p>
                Now on <span className="font-semibold text-ink">{fromPlan}</span>
                {current ? <> · {priceLine(current)}</> : null}
              </p>
              {pending ? (
                <p>
                  A change to <span className="font-semibold text-ink">{pendingPlan}</span> is scheduled
                  {data?.pending_effective_at ? ` for ${formatDate(data.pending_effective_at)}` : ""}. Choosing
                  another plan replaces it.
                </p>
              ) : null}
            </div>
            {blockedCode !== null ? (
              <InlineAlert tone="warning" title="This subscription can't change plan right now.">
                {refusalGuidance(blockedCode)}
              </InlineAlert>
            ) : null}
            {formFailure?.guidance ? <p className="text-sm text-ink-soft">{formFailure.guidance}</p> : null}
            {choices.length === 0 || selectable.length === 0 ? (
              <p className="text-sm text-muted">
                No other plan is on offer for this product right now. Contact info@opsgrids.com to discuss a
                different plan.
              </p>
            ) : null}
            {choices.length > 0 ? (
              <RadioGroup
                legend="Move to"
                name={groupName}
                value={selected}
                onChange={(value) => {
                  setSelected(value);
                  setPickError(undefined);
                  setFormFailure(null);
                }}
                options={radioOptions}
                helper="Prices are shown only for plans agreed for your organisation. Pricing for other plans is agreed with OpsGrids."
                {...(pickError ? { error: pickError } : {})}
              />
            ) : null}
          </>
        )}
      </FormDialog>

      <ConfirmDialog
        open={preview !== null}
        onClose={() => {
          setPreview(null);
          setConfirmFailure(null);
        }}
        onConfirm={() => void confirm()}
        tone="primary"
        title={preview ? confirmTitle(product, toPlan, preview) : "Confirm plan change"}
        body={
          preview
            ? previewSentence({
                product,
                fromPlan,
                toPlan,
                preview,
                pendingPlan,
                pendingAt: data?.pending_effective_at ?? null,
              })
            : ""
        }
        confirmLabel={preview ? confirmLabel(preview) : "Change plan"}
        pendingLabel="Saving…"
        cancelLabel="Back"
        pending={confirming}
        {...(confirmFailure ? { error: confirmFailure.message } : {})}
        {...(confirmFailure?.reference ? { reference: confirmFailure.reference } : {})}
      >
        {preview ? (
          <PreviewNotes notes={previewNotes(preview, { pendingPlan, productNames })} guidance={confirmFailure?.guidance ?? null} />
        ) : undefined}
      </ConfirmDialog>
    </>
  );
}

function PreviewNotes({ notes, guidance }: { notes: string[]; guidance: string | null }) {
  if (notes.length === 0 && guidance === null) return null;
  return (
    <div className="flex flex-col gap-2">
      {notes.length > 0 ? (
        <ul className="flex list-disc flex-col gap-1 pl-4 text-sm text-ink-soft">
          {notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      ) : null}
      {guidance ? <p className="text-sm text-ink-soft">{guidance}</p> : null}
    </div>
  );
}
