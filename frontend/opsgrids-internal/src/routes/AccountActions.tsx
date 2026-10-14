import { useState } from "react";
import type { ReactNode } from "react";
import { Dialog, FormDialog, RadioGroup, Skeleton, TextArea, TextField } from "@opsgrids/ui";

import { getOrganizationPricing, postOverride } from "@/api/client";
import type { OverrideRequest, OverrideResult, PricingOrgRef, ProductPricing } from "@/api/types";
import { useAsync } from "@/lib/useAsync";
import { AccountStatusPill, Button, ErrorState, Money } from "@/ui";
import { CheckCircleIcon } from "@/ui/icons";

/** Which account-detail action modal is open, or none. */
export type ActionKind = null | "suspend" | "reactivate" | "comp";

interface ActionProps {
  org: PricingOrgRef;
  onClose: () => void;
  onDone: (result: OverrideResult) => void;
}

/* ------------------------- shared building blocks ------------------------- */

/**
 * The endpoint note every action carries, for the operator who wants to know what the form would
 * POST to. Developer copy, so it sits in a collapsed disclosure rather than between the fields
 * and the submit button on a phone.
 */
function DelegationNote({ children }: { children: ReactNode }) {
  return (
    <details className="rounded-tile bg-ground px-3 text-caption leading-relaxed text-muted">
      <summary className="hit-44 flex cursor-pointer items-center font-semibold text-ink-soft">
        Details
      </summary>
      <p className="pb-3">{children}</p>
    </details>
  );
}

/** The success panel shown after an override is accepted. There is no structured audit row in the
 *  response to quote — `OverrideOut.result` is whatever JSON the owning service happened to
 *  return — so this shows only what the console itself knows for certain: which action, and who it
 *  delegated to. */
function ResultPanel({ result, onClose }: { result: OverrideResult; onClose: () => void }) {
  return (
    <div className="flex flex-col items-center gap-4 py-4 text-center" role="status" aria-live="polite">
      <span className="grid size-14 place-items-center rounded-full bg-present-soft text-present">
        <CheckCircleIcon className="size-7" />
      </span>
      <div className="flex flex-col gap-1">
        <p className="text-base font-bold">Override accepted</p>
        <p className="text-sm text-muted">Delegated to {result.delegated_to}-service.</p>
      </div>
      <Button onClick={onClose} className="w-full">
        Done
      </Button>
    </div>
  );
}

interface ActionDialogProps {
  title: string;
  description: string;
  result: OverrideResult | null;
  error: string | null;
  submitting: boolean;
  submitLabel: string;
  pendingLabel: string;
  /** The submit is destructive (suspend): drawn in the destructive colour. */
  destructive?: boolean;
  onClose: () => void;
  onSubmit: () => void;
  children: ReactNode;
}

/** The shared frame: a `FormDialog` (scrolling body, sticky submit, focus trap) that becomes the
 *  result panel once the override is accepted. */
function ActionDialog({
  title,
  description,
  result,
  error,
  submitting,
  submitLabel,
  pendingLabel,
  destructive = false,
  onClose,
  onSubmit,
  children,
}: ActionDialogProps) {
  if (result) {
    return (
      <Dialog open onClose={onClose} title={title} description={description}>
        <ResultPanel result={result} onClose={onClose} />
      </Dialog>
    );
  }
  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={onSubmit}
      title={title}
      description={description}
      submitLabel={submitLabel}
      pendingLabel={pendingLabel}
      pending={submitting}
      destructive={destructive}
      {...(error !== null ? { error } : {})}
    >
      {children}
    </FormDialog>
  );
}

/** The subscription picker suspend and reactivate share. */
function SubscriptionPicker({
  candidates,
  chosen,
  onChange,
}: {
  candidates: ProductPricing[];
  chosen: string;
  onChange: (id: string) => void;
}) {
  return (
    <RadioGroup
      legend="Which subscription?"
      name="subscription"
      value={chosen}
      onChange={onChange}
      options={candidates.map((p) => ({
        value: p.subscription?.id ?? "",
        label: p.product_name,
        aside: <AccountStatusPill status={p.subscription?.state ?? "unknown"} />,
      }))}
    />
  );
}

/** A tiny submit hook so each form shares the submitting / error / result lifecycle. */
function useOverride(onDone: (r: OverrideResult) => void) {
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<OverrideResult | null>(null);

  async function run(request: OverrideRequest) {
    if (submitting) return;
    setError(null);
    setSubmitting(true);
    try {
      const res = await postOverride(request);
      setResult(res);
      onDone(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : "The override was rejected.");
    } finally {
      setSubmitting(false);
    }
  }

  return { submitting, error, result, run };
}

/* -------------------------------- suspend -------------------------------- */

/** `POST /admin/overrides` requires a `subscription_id` for a suspend — a real subscription UUID
 *  that only ever shows up in one place across the whole admin surface: the per-product pricing
 *  composite (`ProductSubscriptionOut.id`). `/admin/subscriptions` (the bulk list) carries no id at
 *  all. So suspending means picking *which* subscription off that same pricing read, not a
 *  free-standing subscription picker. */
export function SuspendAction({ org, onClose, onDone }: ActionProps) {
  const [reason, setReason] = useState("");
  const [reasonError, setReasonError] = useState<string | undefined>(undefined);
  const [subscriptionId, setSubscriptionId] = useState<string>("");
  const { submitting, error, result, run } = useOverride(onDone);

  const pricing = useAsync(() => getOrganizationPricing(org.organization_id), [org.organization_id]);
  const subscribed: ProductPricing[] =
    pricing.status === "success" ? pricing.data.products.filter((p) => p.subscription !== null) : [];
  const chosen = subscriptionId || subscribed[0]?.subscription?.id || "";

  function submit() {
    if (!chosen) return;
    if (reason.trim().length === 0) {
      setReasonError("Enter a reason.");
      return;
    }
    setReasonError(undefined);
    void run({
      action: "suspend",
      organization_id: org.organization_id,
      subscription_id: chosen,
      reason: reason.trim(),
    });
  }

  return (
    <ActionDialog
      title={`Suspend ${org.name}`}
      description="De-entitles the account on its next access-token refresh (≤ 15 min)."
      result={result}
      error={error}
      submitting={submitting}
      submitLabel="Suspend account"
      pendingLabel="Suspending…"
      destructive
      onClose={onClose}
      onSubmit={submit}
    >
      {pricing.status === "loading" ? (
        <Skeleton className="h-24 w-full" label="Loading subscriptions" />
      ) : pricing.status === "error" ? (
        <ErrorState message={pricing.error.message} />
      ) : subscribed.length === 0 ? (
        <p className="rounded-tile bg-ground px-3 py-2.5 text-sm text-muted">
          {org.name} has no subscription on any product — there is nothing to suspend.
        </p>
      ) : (
        <SubscriptionPicker candidates={subscribed} chosen={chosen} onChange={setSubscriptionId} />
      )}

      <TextArea
        label="Reason (required)"
        required
        rows={3}
        value={reason}
        error={reasonError}
        onChange={(e) => setReason(e.target.value)}
        placeholder="e.g. Non-payment after dunning window closed"
      />
      <DelegationNote>
        POSTs <span className="mono">{"{action: \"suspend\"}"}</span> to{" "}
        <span className="mono">/api/v1/admin/overrides</span>, which delegates to
        subscription-service's <span className="mono">/internal</span> suspend. The activation is
        projected off, and the gateway then answers the customer with a 402.
      </DelegationNote>
    </ActionDialog>
  );
}

/* ------------------------------ reactivate ------------------------------- */

/** `delegation.py`'s bound on `reactivate`'s reason — an over-long one is a 422 at console's own
 *  boundary rather than a 503 from subscription-service's refusal. */
const MAX_REACTIVATE_REASON_LENGTH = 200;

/** `POST /admin/overrides` now resolves `reactivate` too, the same shape as `suspend`: a real
 *  subscription id off the per-product pricing composite (`/admin/subscriptions` carries none),
 *  and — unlike suspend — a reason that is *required*, since lifting a hold is exactly the act
 *  whose why an audit trail must carry. */
export function ReactivateAction({ org, onClose, onDone }: ActionProps) {
  const [reason, setReason] = useState("");
  const [reasonError, setReasonError] = useState<string | undefined>(undefined);
  const [subscriptionId, setSubscriptionId] = useState<string>("");
  const { submitting, error, result, run } = useOverride(onDone);

  const pricing = useAsync(() => getOrganizationPricing(org.organization_id), [org.organization_id]);
  // Only a suspended subscription is a candidate — reactivating one already running is not a
  // state the owning service's own change accepts (a 409 the operator would just have to retry).
  const suspended: ProductPricing[] =
    pricing.status === "success"
      ? pricing.data.products.filter((p) => p.subscription?.state === "suspended")
      : [];
  const chosen = subscriptionId || suspended[0]?.subscription?.id || "";
  const trimmedReason = reason.trim();

  function submit() {
    if (!chosen) return;
    if (trimmedReason.length === 0) {
      setReasonError("Enter a reason.");
      return;
    }
    if (trimmedReason.length > MAX_REACTIVATE_REASON_LENGTH) {
      setReasonError(`Keep the reason to ${MAX_REACTIVATE_REASON_LENGTH} characters.`);
      return;
    }
    setReasonError(undefined);
    void run({
      action: "reactivate",
      organization_id: org.organization_id,
      subscription_id: chosen,
      reason: trimmedReason,
    });
  }

  return (
    <ActionDialog
      title={`Reactivate ${org.name}`}
      description="Restores entitlement; the customer regains access on the next access-token refresh (≤ 15 min)."
      result={result}
      error={error}
      submitting={submitting}
      submitLabel="Reactivate account"
      pendingLabel="Reactivating…"
      onClose={onClose}
      onSubmit={submit}
    >
      {pricing.status === "loading" ? (
        <Skeleton className="h-24 w-full" label="Loading subscriptions" />
      ) : pricing.status === "error" ? (
        <ErrorState message={pricing.error.message} />
      ) : suspended.length === 0 ? (
        <p className="rounded-tile bg-ground px-3 py-2 text-sm text-muted">
          {org.name} has no suspended subscription on any product — there is nothing to
          reactivate.
        </p>
      ) : (
        <SubscriptionPicker candidates={suspended} chosen={chosen} onChange={setSubscriptionId} />
      )}

      <TextArea
        label="Reason (required)"
        required
        rows={3}
        maxLength={MAX_REACTIVATE_REASON_LENGTH}
        value={reason}
        error={reasonError}
        helper={`${trimmedReason.length}/${MAX_REACTIVATE_REASON_LENGTH}`}
        onChange={(e) => setReason(e.target.value)}
        placeholder="e.g. Payment received — dunning hold cleared"
      />
      <DelegationNote>
        POSTs <span className="mono">{"{action: \"reactivate\"}"}</span> to{" "}
        <span className="mono">/api/v1/admin/overrides</span>, which delegates to
        subscription-service's <span className="mono">/internal</span> reactivate. Entitlement
        is restored on the next access-token refresh.
      </DelegationNote>
    </ActionDialog>
  );
}

/* ------------------------------ comp grant ------------------------------- */

/** `POST /admin/overrides` for `comp_grant` accepts only `organization_id`, `amount_minor`,
 *  `currency` and `reason` — there is no `is_comp` flag and no `valid_until`, so this form no
 *  longer offers either: showing a control the server silently drops would be worse than not
 *  showing it (see `types.ts`). */
export function CompGrantAction({ org, onClose, onDone }: ActionProps) {
  const [amount, setAmount] = useState("");
  const [reason, setReason] = useState("");
  const [amountError, setAmountError] = useState<string | undefined>(undefined);
  const [reasonError, setReasonError] = useState<string | undefined>(undefined);
  const { submitting, error, result, run } = useOverride(onDone);

  const amountMinor = Math.round(Number(amount) * 100);

  function submit() {
    const badAmount = amount === "" || !(amountMinor > 0) ? "Enter an amount greater than zero." : undefined;
    const badReason = reason.trim().length === 0 ? "Enter a reason." : undefined;
    setAmountError(badAmount);
    setReasonError(badReason);
    if (badAmount || badReason) return;
    void run({
      action: "comp_grant",
      organization_id: org.organization_id,
      amount_minor: amountMinor,
      currency: "INR",
      reason: reason.trim(),
    });
  }

  return (
    <ActionDialog
      title={`Grant credit — ${org.name}`}
      description="An append-only credit-ledger entry. Nothing is mutated."
      result={result}
      error={error}
      submitting={submitting}
      submitLabel="Grant credit"
      pendingLabel="Granting…"
      onClose={onClose}
      onSubmit={submit}
    >
      <TextField
        label="Amount (₹)"
        type="number"
        min="1"
        step="1"
        required
        inputMode="numeric"
        value={amount}
        error={amountError}
        onChange={(e) => setAmount(e.target.value)}
        placeholder="45000"
        className="tnum"
      />

      <TextField
        label="Reason (required)"
        required
        value={reason}
        error={reasonError}
        onChange={(e) => setReason(e.target.value)}
        placeholder="e.g. 3 months complimentary — migration goodwill"
      />

      {amountMinor > 0 ? (
        <p className="text-sm text-muted">
          Grants <Money minor={amountMinor} currency="INR" className="font-semibold text-ink" /> of
          credit, with no expiry — the live ledger has no expiry field on a grant made this way.
        </p>
      ) : null}

      <DelegationNote>
        POSTs <span className="mono">{"{action: \"comp_grant\"}"}</span> to{" "}
        <span className="mono">/api/v1/admin/overrides</span> → billing-service writes one
        append-only credit-ledger row.
      </DelegationNote>
    </ActionDialog>
  );
}
