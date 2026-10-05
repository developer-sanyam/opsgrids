import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import type { MouseEvent } from "react";
import { CheckboxGroup, ConfirmDialog, FormDialog, InlineAlert, RadioGroup, Select, TextArea } from "@opsgrids/ui";
import type { RadioOption } from "@opsgrids/ui";

import { addProduct, getAddProductOptions } from "@/api/client";
import type { AddProductOption, AddProductOptions, AddProductResult, PricingOrgRef } from "@/api/types";
import { useAuth } from "@/auth/context";
import { formatMoney } from "@/lib/money";
import { ENTITLEMENT_LAG_COPY, failureKind, notMadeGuidance, unknownOutcomeGuidance } from "@/lib/planChangeCopy";
import {
  ADD_ROLE_HINT,
  PAID_ACKNOWLEDGEMENT,
  addPreviewSentence,
  addResultSentence,
  addResultTitle,
} from "@/lib/staffSubscriptionCopy";
import { Button, Card, SectionHeader, Skeleton, Tag } from "@/ui";
import { failureOf } from "./staffActionFailure";
import type { Failure } from "./staffActionFailure";

const MAX_REASON = 500;
const ACK = "acknowledged";

type OptionsState =
  | { status: "loading" }
  | { status: "error"; failure: Failure }
  | { status: "ready"; data: AddProductOptions };

function intervalWord(interval: string): string {
  return interval === "year" ? "year" : interval === "month" ? "month" : interval.replace("_", " ");
}

function versionDescription(option: AddProductOption): string {
  if (option.blocked_reason) return option.blocked_reason;
  const parts: string[] = [];
  if (option.recurring_minor > 0) {
    // Display only — integer minor units through the shared formatter; billing prices the period.
    parts.push(`${formatMoney(option.recurring_minor, option.currency, { precise: true })} per ${intervalWord(option.billing_interval)}`);
  }
  if (option.prices.some((p) => p.kind === "usage")) parts.push("plus metered usage");
  if (option.bundled_products?.length) parts.push(`includes ${option.bundled_products.join(", ")}`);
  parts.push(
    !option.offers_trial ? "no trial" : option.trial_days ? `${option.trial_days}-day trial` : "standard trial",
  );
  return parts.join(" · ");
}

interface FlowProps {
  org: PricingOrgRef;
  onClose: () => void;
  onDone: (result: AddProductResult, productName: string, planName: string) => void;
}

/**
 * Add product, in two steps, owned by one component mounted only while the flow is open — so
 * closing it resets every field structurally.
 *
 * 1. **Form** — which product (only those the organization has no row for, in any state), which
 *    plan (a version it cannot take is listed, disabled, with the server's reason), how it starts
 *    (a trial by default; a paid start must be acknowledged), and why.
 * 2. **Confirm** — what will happen, in a sentence, with the idempotency key minted here.
 *
 * A refused confirm returns to the form with the server's sentence, a fresh key and the options
 * re-read; an unknown outcome stays put, and *Try again* repeats the same request under the same
 * key — which the server replays or reconciles, never a second subscription.
 */
function AddProductFlow({ org, onClose, onDone }: FlowProps) {
  const planGroup = useId();
  const startGroup = useId();

  const [options, setOptions] = useState<OptionsState>({ status: "loading" });
  const [productKey, setProductKey] = useState("");
  const [versionId, setVersionId] = useState<string | null>(null);
  const [start, setStart] = useState<"trial" | "paid">("trial");
  const [acknowledged, setAcknowledged] = useState<string[]>([]);
  const [reason, setReason] = useState("");
  const [touched, setTouched] = useState(false);

  const [phase, setPhase] = useState<"form" | "confirm">("form");
  const [formFailure, setFormFailure] = useState<Failure | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [confirmFailure, setConfirmFailure] = useState<Failure | null>(null);

  const keyRef = useRef<string | null>(null);
  const confirmingRef = useRef(false);

  const loadOptions = useCallback(() => {
    setOptions({ status: "loading" });
    getAddProductOptions(org.organization_id)
      .then((data) => setOptions({ status: "ready", data }))
      .catch((err: unknown) => setOptions({ status: "error", failure: failureOf(err).failure }));
  }, [org.organization_id]);

  useEffect(() => {
    loadOptions();
  }, [loadOptions]);

  const products = useMemo(() => (options.status === "ready" ? options.data.products : []), [options]);
  const product = products.find((p) => p.product_key === productKey);
  const version = product?.versions.find((v) => v.plan_version_id === versionId);
  const trial = start === "trial";
  const reasonTrimmed = reason.trim();

  const productError = touched && !product ? "Choose a product." : undefined;
  const versionError = touched && product && !version ? "Choose a plan." : undefined;
  const startError =
    touched && version && trial && !version.offers_trial
      ? "This plan sells no trial. Choose a paid start, or another plan."
      : undefined;
  const ackError = touched && !trial && !acknowledged.includes(ACK) ? "Confirm that billing starts now." : undefined;
  const reasonError = !touched
    ? undefined
    : reasonTrimmed.length === 0
      ? "Enter a reason."
      : reasonTrimmed.length > MAX_REASON
        ? `Keep the reason to ${MAX_REASON} characters or fewer.`
        : undefined;

  const versionOptions: RadioOption[] = useMemo(
    () =>
      (product?.versions ?? []).map((v) => ({
        value: v.plan_version_id,
        label: `${v.plan_name} · v${v.version_no}`,
        description: versionDescription(v),
        disabled: v.blocked_code !== null,
        aside: v.is_bespoke ? <Tag>Custom</Tag> : undefined,
      })),
    [product],
  );

  const startOptions: RadioOption[] = [
    {
      value: "trial",
      label: "Trial",
      description: "Free until the trial ends; the billing run then converts it. The default.",
      disabled: version !== undefined && !version.offers_trial,
    },
    {
      value: "paid",
      label: "Paid from today",
      description: "Billing starts now: the first period is claimed for payment at once.",
    },
  ];

  function chooseProduct(key: string) {
    setProductKey(key);
    setVersionId(null);
  }

  function review() {
    setTouched(true);
    setFormFailure(null);
    const valid =
      version !== undefined &&
      version.blocked_code === null &&
      !(trial && !version.offers_trial) &&
      (trial || acknowledged.includes(ACK)) &&
      reasonTrimmed.length > 0 &&
      reasonTrimmed.length <= MAX_REASON;
    if (!valid) return;
    keyRef.current = crypto.randomUUID();
    setConfirmFailure(null);
    setPhase("confirm");
  }

  async function confirm() {
    if (confirmingRef.current || !product || !version || keyRef.current === null) return;
    confirmingRef.current = true;
    setConfirming(true);
    setConfirmFailure(null);
    try {
      const result = await addProduct(
        org.organization_id,
        {
          product_key: product.product_key,
          plan_version_id: version.plan_version_id,
          trial,
          acknowledge_immediate_charge: !trial && acknowledged.includes(ACK),
          reason: reasonTrimmed,
        },
        keyRef.current,
      );
      onDone(result, product.product_name, version.plan_name);
    } catch (err) {
      const { failure, status, code } = failureOf(err);
      const kind = failureKind(status, code);
      if (kind === "refused") {
        keyRef.current = null;
        setPhase("form");
        setFormFailure(failure);
        loadOptions();
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
      }
    } finally {
      confirmingRef.current = false;
      setConfirming(false);
    }
  }

  const dirty = productKey !== "" || reasonTrimmed.length > 0;

  return (
    <>
      <FormDialog
        open
        onClose={onClose}
        onSubmit={review}
        title="Add product"
        description={`${org.name}. A trial by default; a paid start bills at once.`}
        submitLabel="Review"
        {...(formFailure ? { error: formFailure.message } : {})}
        {...(formFailure?.reference ? { reference: formFailure.reference } : {})}
        dirty={dirty}
      >
        {options.status === "loading" ? (
          <div className="flex flex-col gap-3" aria-busy="true">
            <Skeleton className="h-11 w-full" />
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
        ) : products.length === 0 ? (
          <p className="text-sm text-muted">
            There is nothing to add: this organization already has every product on offer. A cancelled
            subscription cannot be reopened or replaced yet.
          </p>
        ) : (
          <>
            {formFailure?.guidance ? <p className="text-sm text-muted">{formFailure.guidance}</p> : null}
            <Select
              label="Product"
              required
              placeholder="Choose a product"
              value={productKey}
              onChange={(e) => chooseProduct(e.target.value)}
              options={products.map((p) => ({ value: p.product_key, label: p.product_name }))}
              {...(options.data.existing.length > 0
                ? {
                    helper: `Not offered — already has: ${options.data.existing
                      .map((e) => `${e.product_key} (${e.state})`)
                      .join(", ")}.`,
                  }
                : {})}
              {...(productError ? { error: productError } : {})}
            />
            {product ? (
              <RadioGroup
                legend="Plan"
                name={planGroup}
                value={versionId}
                onChange={setVersionId}
                options={versionOptions}
                helper="Plans this organization cannot take are listed with the reason."
                {...(versionError ? { error: versionError } : {})}
              />
            ) : null}
            {version && version.overlapping_products.length > 0 ? (
              <InlineAlert tone="warning" title="Already paid for separately">
                This organization already has {version.overlapping_products.join(", ")} on its own
                subscription. Adding this bundle bills both, with no credit.
              </InlineAlert>
            ) : null}
            <RadioGroup
              legend="Start"
              name={startGroup}
              value={start}
              onChange={(value) => setStart(value === "paid" ? "paid" : "trial")}
              options={startOptions}
              {...(startError ? { error: startError } : {})}
            />
            {!trial ? (
              <CheckboxGroup
                label="Billing starts now"
                options={[{ value: ACK, label: PAID_ACKNOWLEDGEMENT }]}
                value={acknowledged}
                onChange={setAcknowledged}
                helper="If the first charge fails, the subscription goes past due and is suspended when the grace window closes."
                {...(ackError ? { error: ackError } : {})}
              />
            ) : null}
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
          </>
        )}
      </FormDialog>

      <ConfirmDialog
        open={phase === "confirm" && product !== undefined && version !== undefined}
        onClose={() => setPhase("form")}
        onConfirm={() => void confirm()}
        tone="primary"
        title="Confirm new subscription"
        body={
          product && version
            ? addPreviewSentence({ organizationName: org.name, productName: product.product_name, option: version, trial })
            : ""
        }
        confirmLabel={trial ? "Start trial" : "Add and start billing"}
        pendingLabel="Adding…"
        cancelLabel="Back"
        pending={confirming}
        {...(confirmFailure ? { error: confirmFailure.message } : {})}
        {...(confirmFailure?.reference ? { reference: confirmFailure.reference } : {})}
      >
        <div className="flex flex-col gap-3 text-sm">
          <ul className="flex flex-col gap-2 text-muted">
            {version?.is_bespoke ? <li>This is a price negotiated for this organization.</li> : null}
            {version && version.overlapping_products.length > 0 ? (
              <li>Bills alongside the separate {version.overlapping_products.join(", ")} subscription, with no credit.</li>
            ) : null}
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
  title: string;
  sentence: string;
}

/**
 * Add product — give an organization a product it has no subscription for (ADR-0030).
 *
 * Its own card, hosting its own dialogs, so the account page's reload never unmounts them mid-flow.
 * The role hint is a hint: the server refuses anyone who is not `owner` or `billing_ops`. A trial is
 * the default; a paid start bills at once with no mandate check, so it must be acknowledged.
 */
export function AddProductCard({ org, onChanged }: { org: PricingOrgRef; onChanged?: () => void }) {
  const { hasRole } = useAuth();
  const canAdd = hasRole("owner") || hasRole("billing_ops");
  const [open, setOpen] = useState(false);
  const [done, setDone] = useState<DoneView | null>(null);
  const trigger = useRef<HTMLElement | null>(null);
  const hintId = useId();

  const close = useCallback(() => {
    setOpen(false);
    window.requestAnimationFrame(() => trigger.current?.focus());
  }, []);

  const handleDone = useCallback(
    (result: AddProductResult, productName: string, planName: string) => {
      setDone({ title: addResultTitle(result), sentence: addResultSentence(org.name, productName, planName, result) });
      onChanged?.();
      close();
    },
    [close, onChanged, org.name],
  );

  function start(event: MouseEvent<HTMLButtonElement>) {
    trigger.current = event.currentTarget;
    setDone(null);
    setOpen(true);
  }

  return (
    <Card id="add-product" className="flex scroll-mt-6 flex-col gap-4 p-6">
      <SectionHeader
        title="Add product"
        subtitle="Give this organization a product it does not have. A trial by default; a paid start bills at once."
        action={
          <Button
            size="sm"
            variant="outline"
            disabled={!canAdd}
            aria-describedby={canAdd ? undefined : hintId}
            onClick={start}
          >
            Add product
          </Button>
        }
      />
      {!canAdd ? (
        <p id={hintId} className="text-xs text-faint">
          {ADD_ROLE_HINT}
        </p>
      ) : null}

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

      {open ? <AddProductFlow org={org} onClose={close} onDone={handleDone} /> : null}
    </Card>
  );
}
