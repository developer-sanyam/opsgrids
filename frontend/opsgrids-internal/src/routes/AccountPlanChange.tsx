import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import type { MouseEvent } from "react";
import { ConfirmDialog, FormDialog, InlineAlert, RadioGroup, TextArea } from "@opsgrids/ui";
import type { RadioOption } from "@opsgrids/ui";

import { changePlan, getPlanOptions, previewPlanChange } from "@/api/client";
import { ApiError } from "@/api/http";
import type {
  PlanChangePreview,
  PlanChangeResult,
  PlanOption,
  PlanOptions,
  PricingOrgRef,
  ProductPricing,
} from "@/api/types";
import { useAuth } from "@/auth/context";
import { formatDate } from "@/lib/format";
import { formatMoney } from "@/lib/money";
import { useLivePricing } from "@/lib/useLivePricing";
import {
  BILLING_RUN_COPY,
  ENTITLEMENT_LAG_COPY,
  ROLE_HINT,
  failureKind,
  notMadeGuidance,
  previewSentence,
  refusalGuidance,
  resultSentence,
  resultTitle,
  unknownOutcomeGuidance,
} from "@/lib/planChangeCopy";
import {
  AccountStatusPill,
  Button,
  Card,
  DataTable,
  EmptyState,
  ErrorState,
  Money,
  SectionHeader,
  Skeleton,
  Tag,
} from "@/ui";
import type { Column } from "@/ui";

const CHANGEABLE_STATES = new Set(["trial", "active"]);

/** What a dialog reports when something failed: the server's sentence verbatim, and the reference. */
interface Failure {
  message: string;
  reference: string | undefined;
  guidance: string | null;
}

function failureOf(error: unknown): { failure: Failure; status: number | null; code: string | null } {
  if (error instanceof ApiError) {
    return {
      failure: {
        message: error.message,
        reference: error.requestId ?? undefined,
        guidance: refusalGuidance(error.code),
      },
      status: error.status,
      code: error.code,
    };
  }
  // A request that never got an answer (offline, a reset connection): the outcome of a write is
  // not known, which `failureKind` reads from the missing status.
  const message = error instanceof Error ? error.message : "The request did not complete.";
  return { failure: { message, reference: undefined, guidance: null }, status: null, code: null };
}

/**
 * A plan version's name, from the dialog's **own fresh reads** — never the card's snapshot, which
 * reloads only after the card's own actions and can be minutes old (a customer's own upgrade, or
 * the billing run applying a scheduled change, moves the subscription without telling it).
 */
function planNameFor(id: string | null, options: PlanOptions | null): string | null {
  if (id === null || options === null) return null;
  const sub = options.subscription;
  if (sub.plan_version_id === id && sub.plan_name) return sub.plan_name;
  if (sub.pending_plan_version_id === id && sub.pending_plan_name) return sub.pending_plan_name;
  return options.options.find((o) => o.plan_version_id === id)?.plan_name ?? null;
}

function intervalWord(interval: string): string {
  return interval === "year" ? "year" : interval === "month" ? "month" : interval.replace("_", " ");
}

/* ---------------------------------- the flow ---------------------------------- */

interface FlowProps {
  org: PricingOrgRef;
  product: ProductPricing;
  onClose: () => void;
  /** The live subscription may have changed (a stale confirm, an unknown outcome): reload it. */
  onRefresh: () => void;
  onDone: (result: PlanChangeResult, toPlan: string) => void;
}

type OptionsState =
  | { status: "loading" }
  | { status: "error"; failure: Failure }
  | { status: "ready"; data: PlanOptions };

const MAX_REASON = 500;

/**
 * Change plan, in three steps, owned by one component that is mounted only while the flow is open —
 * so closing it resets every field structurally, and nothing here survives to the next open.
 *
 * 1. **Form** (`FormDialog`) — pick a plan and say why. The choices come from live reads of catalog
 *    and the subscription; a version this subscription cannot take is listed, disabled, with the
 *    server's reason.
 * 2. **Confirm** (`ConfirmDialog`, stacked over the form so *Back* keeps what was typed) — the
 *    server's own preview of what would happen, in a sentence, with the idempotency key minted
 *    here. A refusal from the preview never reaches this step.
 * 3. **Result** — handed to the card, which announces it in a live region.
 *
 * A confirm that is **refused** (a stale state, a rule) returns to the form with the server's
 * sentence and a fresh key, because repeating a refusal cannot change it. One whose **outcome is
 * unknown** stays put: *Try again* repeats the same request under the same key, which the server
 * replays or reconciles — never a second change.
 */
function PlanChangeFlow({ org, product, onClose, onRefresh, onDone }: FlowProps) {
  const groupName = useId();

  const [options, setOptions] = useState<OptionsState>({ status: "loading" });
  const [selected, setSelected] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [touched, setTouched] = useState(false);

  const [phase, setPhase] = useState<"form" | "confirm">("form");
  const [preview, setPreview] = useState<PlanChangePreview | null>(null);
  const [checking, setChecking] = useState(false);
  const [formFailure, setFormFailure] = useState<Failure | null>(null);

  const [confirming, setConfirming] = useState(false);
  const [confirmFailure, setConfirmFailure] = useState<Failure | null>(null);

  // One key per confirm screen: minted when the preview arrives, reused for every retry of that
  // confirm, replaced after a definitive refusal.
  const keyRef = useRef<string | null>(null);
  const confirmingRef = useRef(false);

  const productKey = product.product_key;
  const loadOptions = useCallback(() => {
    setOptions({ status: "loading" });
    getPlanOptions(org.organization_id, productKey)
      .then((data) => setOptions({ status: "ready", data }))
      .catch((err: unknown) => setOptions({ status: "error", failure: failureOf(err).failure }));
  }, [org.organization_id, productKey]);

  useEffect(() => {
    loadOptions();
  }, [loadOptions]);

  const choices = useMemo(
    () => (options.status === "ready" ? options.data.options : []),
    [options],
  );
  const target: PlanOption | undefined = choices.find((o) => o.plan_version_id === selected);
  const reasonTrimmed = reason.trim();
  const selectionError = touched && selected === null ? "Choose the plan to move to." : undefined;
  const reasonError = !touched
    ? undefined
    : reasonTrimmed.length === 0
      ? "Enter a reason."
      : reasonTrimmed.length > MAX_REASON
        ? `Keep the reason to ${MAX_REASON} characters or fewer.`
        : undefined;

  const radioOptions: RadioOption[] = useMemo(
    () =>
      choices.map((o) => ({
        value: o.plan_version_id,
        label: `${o.plan_name} · v${o.version_no}`,
        description: o.blocked_reason ?? optionDescription(o),
        disabled: o.is_current || o.blocked_code !== null,
        aside: o.is_current ? (
          <Tag>Current plan</Tag>
        ) : o.is_pending ? (
          <Tag>Scheduled</Tag>
        ) : o.is_bespoke ? (
          <Tag>Custom</Tag>
        ) : undefined,
      })),
    [choices],
  );

  const optionsReady = options.status === "ready";
  const blockedByState = optionsReady && options.data.subscription.change_blocked_code !== null;

  async function review() {
    setTouched(true);
    setFormFailure(null);
    if (!optionsReady || selected === null || reasonTrimmed.length === 0 || reasonTrimmed.length > MAX_REASON) {
      return;
    }
    setChecking(true);
    try {
      const answer = await previewPlanChange(org.organization_id, productKey, {
        plan_version_id: selected,
        timing: "auto",
      });
      if (answer.refusal_code !== null) {
        // The refusal is the answer; the confirm step is never offered for it.
        setFormFailure({
          message: answer.refusal_detail ?? "This change cannot be made.",
          reference: undefined,
          guidance: refusalGuidance(answer.refusal_code),
        });
        if (answer.refusal_code === "not_offerable") {
          // The list is stale: show what is on offer now, and drop the choice it no longer has.
          setSelected(null);
          loadOptions();
        }
        return;
      }
      keyRef.current = crypto.randomUUID();
      setConfirmFailure(null);
      setPreview(answer);
      setPhase("confirm");
    } catch (err) {
      const { failure, status, code } = failureOf(err);
      // A preview is a read: when it fails, nothing was changed, and the copy says so.
      setFormFailure(
        failureKind(status, code) === "not_made" ? { ...failure, guidance: notMadeGuidance(code) } : failure,
      );
    } finally {
      setChecking(false);
    }
  }

  async function confirm() {
    if (confirmingRef.current || preview === null || selected === null || keyRef.current === null) return;
    confirmingRef.current = true;
    setConfirming(true);
    setConfirmFailure(null);
    try {
      const result = await changePlan(
        org.organization_id,
        productKey,
        { plan_version_id: selected, timing: "auto", reason: reasonTrimmed, expected: preview.expected },
        keyRef.current,
      );
      onDone(result, target?.plan_name ?? "the new plan");
    } catch (err) {
      const { failure, status, code } = failureOf(err);
      const kind = failureKind(status, code);
      if (kind === "refused") {
        // A refusal cannot be changed by repeating it: back to the form, with the server's
        // sentence, a fresh key for whatever is tried next, and the live state reloaded.
        keyRef.current = null;
        setPhase("form");
        setFormFailure(failure);
        if (code === "not_offerable") setSelected(null);
        loadOptions();
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
        // The change may have landed: show the live state behind the dialog.
        if (kind === "unknown") onRefresh();
      }
    } finally {
      confirmingRef.current = false;
      setConfirming(false);
    }
  }

  const freshOptions = options.status === "ready" ? options.data : null;
  // "Now on …" from the plan-options read made when this dialog opened; the confirm sentence from
  // the pins the preview was computed against — the state the confirm will be checked against.
  const fromPlan = freshOptions?.subscription.plan_name ?? "their current plan";
  const previewFromPlan =
    (preview && planNameFor(preview.expected.plan_version_id, freshOptions)) ?? "their current plan";
  const previewPendingPlan = preview ? planNameFor(preview.expected.pending_plan_version_id, freshOptions) : null;
  const dirty = selected !== null || reasonTrimmed.length > 0;

  return (
    <>
      <FormDialog
        open
        onClose={onClose}
        onSubmit={() => void review()}
        title={`Change plan — ${product.product_name}`}
        description={`${org.name}. A trial switches now; a paying subscription switches at its next billing period.`}
        submitLabel="Review change"
        pendingLabel="Checking…"
        pending={checking}
        {...(formFailure ? { error: formFailure.message } : {})}
        {...(formFailure?.reference ? { reference: formFailure.reference } : {})}
        dirty={dirty}
      >
        {options.status === "loading" ? (
          <div className="flex flex-col gap-3" aria-busy="true">
            <Skeleton className="h-16 w-full" />
            <Skeleton className="h-16 w-full" />
            <Skeleton className="h-24 w-full" />
          </div>
        ) : options.status === "error" ? (
          <InlineAlert
            tone="danger"
            title={options.failure.message}
            {...(options.failure.reference ? { reference: options.failure.reference } : {})}
            action={
              <Button size="sm" variant="outline" onClick={loadOptions}>
                Try again
              </Button>
            }
          />
        ) : (
          <>
            <p className="text-sm text-muted">
              Now on <span className="font-semibold text-ink">{fromPlan}</span>
              {options.data.subscription.pending_plan_name ? (
                <>
                  , with a change already scheduled to{" "}
                  <span className="font-semibold text-ink">{options.data.subscription.pending_plan_name}</span>
                </>
              ) : null}
              .
            </p>
            {blockedByState ? (
              <InlineAlert
                tone="warning"
                title={`A ${options.data.subscription.state} subscription cannot change plan.`}
              >
                {refusalGuidance("state")}
              </InlineAlert>
            ) : null}
            {formFailure?.guidance ? <p className="text-sm text-muted">{formFailure.guidance}</p> : null}
            {choices.length === 0 ? (
              <p className="text-sm text-muted">
                No other plan is on offer for this product. A plan becomes available once catalog
                publishes it.
              </p>
            ) : (
              <RadioGroup
                legend="Move to"
                name={groupName}
                value={selected}
                onChange={setSelected}
                options={radioOptions}
                helper="Plans this subscription cannot take are listed with the reason."
                {...(selectionError ? { error: selectionError } : {})}
              />
            )}
            <TextArea
              label="Reason"
              required
              rows={3}
              maxLength={MAX_REASON}
              counterFrom={400}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              onBlur={() => setTouched(true)}
              helper="Recorded on the staff audit trail only. Not sent to the customer."
              {...(reasonError ? { error: reasonError } : {})}
            />
          </>
        )}
      </FormDialog>

      <ConfirmDialog
        open={phase === "confirm" && preview !== null}
        onClose={() => setPhase("form")}
        onConfirm={() => void confirm()}
        tone="primary"
        title="Confirm plan change"
        body={
          preview
            ? previewSentence({
                organizationName: org.name,
                fromPlan: previewFromPlan,
                toPlan: target?.plan_name ?? "the new plan",
                preview,
                pendingPlan: previewPendingPlan,
              })
            : ""
        }
        confirmLabel="Confirm change"
        pendingLabel="Changing plan…"
        cancelLabel="Back"
        pending={confirming}
        {...(confirmFailure ? { error: confirmFailure.message } : {})}
        {...(confirmFailure?.reference ? { reference: confirmFailure.reference } : {})}
      >
        {preview ? (
          <ConfirmDetails
            preview={preview}
            target={target}
            reason={reasonTrimmed}
            failureGuidance={confirmFailure?.guidance ?? null}
          />
        ) : undefined}
      </ConfirmDialog>
    </>
  );
}

function optionDescription(option: PlanOption): string {
  const parts: string[] = [];
  if (option.recurring_minor > 0) {
    // Display only — the preview decides direction. Integer minor units through the shared formatter.
    parts.push(`${formatMoney(option.recurring_minor, option.currency, { precise: true })} per ${intervalWord(option.billing_interval)}`);
  }
  if (option.prices.some((p) => p.kind === "usage")) parts.push("plus metered usage");
  if (option.bundled_products?.length) parts.push(`includes ${option.bundled_products.join(", ")}`);
  return parts.join(" · ");
}

function ConfirmDetails({
  preview,
  target,
  reason,
  failureGuidance,
}: {
  preview: PlanChangePreview;
  target: PlanOption | undefined;
  reason: string;
  failureGuidance: string | null;
}) {
  return (
    <div className="flex flex-col gap-3 text-sm">
      <ul className="flex flex-col gap-2 text-muted">
        {preview.effective === "next_period" ? (
          <li>
            Takes effect at the end of the current billing period — {formatDate(preview.effective_at)}{" "}
            (UTC).
          </li>
        ) : null}
        {preview.would_clear_pending ? <li>This replaces the change already scheduled.</li> : null}
        {preview.entitled_added.length > 0 ? <li>Adds access to: {preview.entitled_added.join(", ")}.</li> : null}
        {preview.entitled_removed.length > 0 ? (
          <li>Removes access to: {preview.entitled_removed.join(", ")}.</li>
        ) : null}
        {preview.usage_closed_now ? (
          <li>
            A usage-based price is involved. The comparison covers the recurring price only. Usage
            metered so far this period is invoiced at the current plan&apos;s usage rates when the plan
            changes; from then on, usage is billed at the new plan&apos;s.
          </li>
        ) : preview.usage_priced ? (
          <li>
            A usage-based price is involved. The comparison covers the recurring price only; metered
            usage is billed on top — at the current plan&apos;s rates until the change takes effect,
            and at the new plan&apos;s after.
          </li>
        ) : null}
        {target?.is_bespoke ? <li>This is a price negotiated for this organization.</li> : null}
        <li>
          Reason: <span className="text-ink">{reason}</span>
        </li>
      </ul>
      <p className="text-xs text-faint">{ENTITLEMENT_LAG_COPY}</p>
      {preview.effective === "next_period" ? <p className="text-xs text-faint">{BILLING_RUN_COPY}</p> : null}
      {failureGuidance ? <p className="text-sm text-muted">{failureGuidance}</p> : null}
    </div>
  );
}

/* ----------------------------------- the card ----------------------------------- */

interface DoneView {
  title: string;
  sentence: string;
}

/**
 * Change plan — the staff control for what a subscription is billed on (ADR-0029).
 *
 * Driven by the **live pricing composite**, not the commercial projection: the projection carries
 * no subscription id, and a scheduled change emits no event, so only the live read can show one.
 * It is its own card, with the dialogs hosted here rather than in the page, so the account page's
 * reload never unmounts them mid-flow.
 *
 * The role hint is a hint — the server refuses the write to anyone who is not `owner` or
 * `billing_ops` — and a paying subscription's change waiting for its next period is the only thing
 * staff can ask for; goodwill for a customer who should have the new plan sooner is a comp credit.
 */
export function PlanChangeCard({ org }: { org: PricingOrgRef }) {
  const { hasRole } = useAuth();
  const canChange = hasRole("owner") || hasRole("billing_ops");
  const [reloadKey, setReloadKey] = useState(0);
  const [flow, setFlow] = useState<ProductPricing | null>(null);
  const [done, setDone] = useState<DoneView | null>(null);
  const trigger = useRef<HTMLElement | null>(null);
  const hintId = useId();

  const live = useLivePricing(org.organization_id, reloadKey);
  const refresh = useCallback(() => setReloadKey((k) => k + 1), []);

  const subscribed = useMemo(() => {
    if (live.status !== "ready") return [];
    return live.data.products
      .filter((p) => p.subscription !== null)
      .sort((a, b) => a.product_name.localeCompare(b.product_name));
  }, [live]);

  const closeFlow = useCallback(() => {
    setFlow(null);
    // The control that opened the dialog is still on the page (the reload is quiet), so focus
    // goes back to it rather than to the top of the document.
    window.requestAnimationFrame(() => trigger.current?.focus());
  }, []);

  const handleDone = useCallback(
    (result: PlanChangeResult, toPlan: string) => {
      setDone({ title: resultTitle(result), sentence: resultSentence(org.name, toPlan, result) });
      refresh();
      closeFlow();
    },
    [closeFlow, org.name, refresh],
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
      key: "state",
      header: "State",
      render: (p) => (p.subscription ? <AccountStatusPill status={p.subscription.state} /> : null),
    },
    {
      key: "plan",
      header: "Plan",
      render: (p) => {
        const sub = p.subscription;
        if (!sub) return null;
        return (
          <div className="flex flex-col gap-1">
            <span className="font-semibold text-ink">{sub.plan_name ?? "Unknown plan"}</span>
            {p.current_price ? (
              <span className="text-xs text-muted">
                <Money minor={p.current_price.amount_minor} currency={p.current_price.currency} precise /> /{" "}
                {intervalWord(p.current_price.billing_interval)}
              </span>
            ) : null}
            {sub.pending_plan_version_id ? (
              <span className="text-xs font-semibold text-late">
                Scheduled: {sub.pending_plan_name ?? "another plan"} on {formatDate(sub.current_period_end)} (UTC)
              </span>
            ) : null}
          </div>
        );
      },
    },
    {
      key: "access",
      header: "Access",
      cellClassName: "hidden md:table-cell",
      headerClassName: "hidden md:table-cell",
      render: (p) => (
        <span className="flex flex-wrap gap-1">
          {(p.subscription?.entitled_products ?? []).map((e) => (
            <Tag key={e}>{e}</Tag>
          ))}
        </span>
      ),
    },
    {
      key: "actions",
      header: "",
      align: "right",
      render: (p) => {
        const sub = p.subscription;
        if (!sub) return null;
        const stateOk = CHANGEABLE_STATES.has(sub.state);
        const enabled = canChange && stateOk;
        return (
          <div className="flex flex-col items-end gap-1">
            <Button
              size="sm"
              variant="outline"
              disabled={!enabled}
              aria-describedby={enabled ? undefined : hintId}
              aria-label={`Change plan for ${p.product_name}`}
              onClick={(event) => open(p, event)}
            >
              Change plan
            </Button>
            {!canChange ? (
              <span className="text-xs text-faint">{ROLE_HINT}</span>
            ) : !stateOk ? (
              <span className="text-xs text-faint">Only a trial or active subscription can change plan.</span>
            ) : null}
          </div>
        );
      },
    },
  ];

  return (
    <Card id="plan-change" className="flex scroll-mt-6 flex-col gap-4 p-6">
      <SectionHeader
        title="Change plan"
        subtitle="A trial switches now. A paying subscription switches at its next billing period — for goodwill sooner than that, grant credit instead."
      />
      <p id={hintId} className="sr-only">
        {canChange ? "Only a trial or an active subscription can change plan." : ROLE_HINT}
      </p>

      {/* The result is announced here. The region exists before there is anything in it, which is
          what makes a screen reader read what arrives. */}
      <div role="status" aria-live="polite" aria-atomic="true">
        {done ? (
          <InlineAlert
            tone="success"
            title={done.title}
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
          <Skeleton className="h-12 w-full" />
        </div>
      ) : live.status === "error" ? (
        <ErrorState message={live.error.message} onRetry={refresh} />
      ) : subscribed.length === 0 ? (
        <EmptyState
          title="No subscriptions to change"
          description="This organization has no subscription yet, so there is no plan to switch."
        />
      ) : (
        <DataTable
          columns={columns}
          rows={subscribed}
          rowKey={(p) => p.product_key}
          caption="Subscriptions whose plan can be changed"
        />
      )}

      {flow ? (
        <PlanChangeFlow org={org} product={flow} onClose={closeFlow} onRefresh={refresh} onDone={handleDone} />
      ) : null}
    </Card>
  );
}
