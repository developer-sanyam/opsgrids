import { useCallback, useId, useMemo, useRef, useState } from "react";
import type { FormEvent } from "react";
import { Dialog } from "@opsgrids/ui";

import { getOrganizationPricing, setOrganizationPrice } from "@/api/client";
import type {
  BillingInterval,
  PricingHistoryEntry,
  PricingOrgRef,
  PricingSubscriptionRef,
  ProductCurrentPrice,
  ProductPricing,
  ProductPublicPrice,
  SetPriceRequest,
  SetPriceResult,
} from "@/api/types";
import { useAuth } from "@/auth/context";
import { daysUntil, formatDateTime } from "@/lib/format";
import { outcomePhrase, predictEffective, summarySentence } from "@/lib/pricingCopy";
import { parseRupeesToPaise } from "@/lib/rupees";
import { useAsync } from "@/lib/useAsync";
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
import { CheckCircleIcon, TagIcon } from "@/ui/icons";

const INPUT =
  "h-11 rounded-tile bg-ground px-3 text-base text-ink sm:text-sm placeholder:text-faint focus:outline-none focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 disabled:cursor-not-allowed disabled:opacity-60";

function toggleClass(active: boolean): string {
  return `rounded-tile border px-3 py-2.5 text-left text-sm font-semibold transition-colors ${
    active ? "border-accent bg-accent-soft text-accent-ink" : "border-line text-muted hover:bg-ground"
  }`;
}

/* ------------------------------- table cells ------------------------------ */

function SubscriptionCell({ subscription }: { subscription: PricingSubscriptionRef | null }) {
  if (!subscription) return <span className="text-sm text-faint">Not subscribed</span>;
  if (subscription.state === "trial") {
    const left = daysUntil(subscription.trial_ends_at);
    return (
      <div className="flex flex-col items-start gap-1">
        <AccountStatusPill status="trial" />
        <span className="text-xs font-medium text-late">
          {left !== null && left >= 0 ? `${left} ${left === 1 ? "day" : "days"} left` : "Trial ended"}
        </span>
      </div>
    );
  }
  return <AccountStatusPill status={subscription.state} />;
}

function intervalLabel(interval: BillingInterval): string {
  return interval === "year" ? "yr" : "mo";
}

function CurrentPriceCell({ price }: { price: ProductCurrentPrice | null }) {
  if (!price) return <span className="text-sm text-faint">Not set</span>;
  return (
    <div className="flex flex-col items-end gap-1">
      <Money minor={price.amount_minor} currency={price.currency} precise className="font-semibold" />
      <span className="flex items-center gap-1.5">
        <span className="text-xs text-muted">/ {intervalLabel(price.billing_interval)}</span>
        {price.is_bespoke ? (
          <span className="inline-flex items-center gap-1 rounded-full bg-accent-soft px-1.5 py-0.5 text-caption font-semibold text-accent-ink">
            <TagIcon className="size-3" /> Custom
          </span>
        ) : (
          <Tag>List price</Tag>
        )}
      </span>
    </div>
  );
}

function PublicPriceCell({ price }: { price: ProductPublicPrice | null }) {
  if (!price) return <span className="text-sm text-faint">—</span>;
  return (
    <div className="flex flex-col items-end gap-0.5">
      <Money minor={price.amount_minor} currency={price.currency} precise className="text-muted" />
      <span className="text-xs text-faint">/ {intervalLabel(price.billing_interval)}</span>
    </div>
  );
}

/* ------------------------------ price history ------------------------------ */

function ProductHistory({ product }: { product: ProductPricing }) {
  const sorted = useMemo(
    () => [...product.history].sort((a, b) => b.created_at.localeCompare(a.created_at)),
    [product.history],
  );

  return (
    <details className="rounded-tile border border-line px-4 py-3">
      <summary className="cursor-pointer text-sm font-semibold text-ink-soft">
        Price history — {product.product_name}
        <span className="ml-2 text-xs font-normal text-faint">
          ({sorted.length} {sorted.length === 1 ? "change" : "changes"})
        </span>
      </summary>
      {sorted.length === 0 ? (
        <p className="pt-3 text-sm text-muted">No price changes recorded yet — still on the list price.</p>
      ) : (
        <ul className="flex flex-col divide-y divide-line pt-3">
          {sorted.map((entry: PricingHistoryEntry) => (
            <li
              key={`${entry.plan_version_id}-${entry.created_at}`}
              className="flex flex-wrap items-start justify-between gap-2 py-2.5 text-sm"
            >
              <div className="flex flex-col gap-0.5">
                <span className="font-semibold text-ink">
                  <Money minor={entry.amount_minor} currency={entry.currency} precise /> /{" "}
                  {intervalLabel(entry.billing_interval)}
                </span>
                <span className="text-xs text-muted">{entry.reason}</span>
              </div>
              <div className="flex flex-col items-end gap-0.5 text-xs text-faint">
                <span className="tnum">{formatDateTime(entry.created_at)}</span>
                <span className="mono">{entry.created_by_email_masked ?? "—"}</span>
              </div>
            </li>
          ))}
        </ul>
      )}
    </details>
  );
}

/* -------------------------------- the dialog ------------------------------- */

interface SetPriceDialogProps {
  org: PricingOrgRef;
  product: ProductPricing;
  onClose: () => void;
  onSuccess: () => void;
}

type Step = "form" | "confirm";

function SetPriceDialog({ org, product, onClose, onSuccess }: SetPriceDialogProps) {
  const formId = useId();
  const amountId = useId();
  const reasonId = useId();
  const amountErrorId = useId();
  const reasonErrorId = useId();

  const hasSubscription = product.subscription !== null;

  const [step, setStep] = useState<Step>("form");
  const [amount, setAmount] = useState("");
  const [amountTouched, setAmountTouched] = useState(false);
  const [billingInterval, setBillingInterval] = useState<BillingInterval>(
    product.public_price?.billing_interval ?? "month",
  );
  const [reason, setReason] = useState("");
  const [reasonTouched, setReasonTouched] = useState(false);
  const [applyToSubscription, setApplyToSubscription] = useState(hasSubscription);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<SetPriceResult | null>(null);
  const submittingRef = useRef(false);

  // One key per dialog open, reused on every retry within this open — a retried click (or a
  // dropped-response retry) never double-applies. Re-opening the dialog remounts this component,
  // which is what earns a fresh key.
  const [idempotencyKey] = useState(() => crypto.randomUUID());

  const parsedAmount = parseRupeesToPaise(amount);
  const amountError = !parsedAmount.ok ? parsedAmount.error : null;
  const reasonTrimmed = reason.trim();
  const reasonError =
    reasonTrimmed.length === 0
      ? "Enter a reason."
      : reasonTrimmed.length > 500
        ? "Keep the reason under 500 characters."
        : null;
  const formValid = parsedAmount.ok && reasonError === null;

  const predicted = predictEffective(hasSubscription, hasSubscription && applyToSubscription);

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (step === "form") {
      setAmountTouched(true);
      setReasonTouched(true);
      if (!formValid) return;
      setStep("confirm");
      return;
    }
    void submit();
  }

  async function submit() {
    if (!parsedAmount.ok || submittingRef.current) return;
    submittingRef.current = true;
    setError(null);
    setSubmitting(true);
    try {
      const request: SetPriceRequest = {
        product_key: product.product_key,
        amount_minor: parsedAmount.minor,
        currency: "INR",
        billing_interval: billingInterval,
        reason: reasonTrimmed,
        apply_to_subscription: hasSubscription ? applyToSubscription : false,
      };
      const res = await setOrganizationPrice(org.organization_id, request, idempotencyKey);
      setResult(res);
      onSuccess();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Setting the price failed.");
    } finally {
      submittingRef.current = false;
      setSubmitting(false);
    }
  }

  const footer =
    step === "form" ? (
      <>
        <Button variant="outline" type="button" onClick={onClose}>
          Cancel
        </Button>
        <Button type="submit" form={formId}>
          Continue
        </Button>
      </>
    ) : (
      <>
        <Button variant="outline" type="button" onClick={() => setStep("form")} disabled={submitting}>
          Back
        </Button>
        <Button type="submit" form={formId} disabled={submitting}>
          {submitting ? "Setting price…" : "Confirm & set price"}
        </Button>
      </>
    );

  return (
    <Dialog
      open
      onClose={onClose}
      title={`Set price — ${product.product_name}`}
      description={org.name}
      footer={result ? undefined : footer}
    >
      {result ? (
        <div role="status" aria-live="polite" className="flex flex-col items-center gap-4 py-4 text-center">
          <span className="grid size-14 place-items-center rounded-full bg-present-soft text-present">
            <CheckCircleIcon className="size-7" />
          </span>
          <div className="flex flex-col gap-1">
            <p className="text-base font-bold">Price set</p>
            <p className="text-sm text-muted">
              {parsedAmount.ok
                ? summarySentence(product.product_name, org.name, parsedAmount.minor, "INR", billingInterval, result.effective)
                : null}
            </p>
            <p className="mono text-xs text-faint">{result.plan_version_id}</p>
          </div>
          <Button onClick={onClose} className="w-full">
            Done
          </Button>
        </div>
      ) : (
        // `noValidate` — this form owns its own validation (inline, field-tied messages that
        // mirror the server's own rules); the browser's native constraint bubble is inconsistent
        // across browsers and screen readers and would otherwise fire instead of `handleSubmit`.
        <form id={formId} className="flex flex-col gap-4 pb-2" onSubmit={handleSubmit} noValidate>
          {error ? (
            <p role="alert" className="rounded-tile bg-danger-soft px-3 py-2.5 text-sm font-medium text-danger">
              {error}
            </p>
          ) : null}

          {step === "form" ? (
            <>
              <div className="flex flex-col gap-1.5">
                <label htmlFor={amountId} className="text-sm font-semibold text-ink-soft">
                  Amount (₹)
                </label>
                <input
                  id={amountId}
                  type="text"
                  inputMode="decimal"
                  autoComplete="off"
                  required
                  value={amount}
                  onChange={(e) => setAmount(e.target.value)}
                  onBlur={() => setAmountTouched(true)}
                  placeholder="2499.50"
                  aria-invalid={amountTouched && amountError ? true : undefined}
                  aria-describedby={amountTouched && amountError ? amountErrorId : undefined}
                  className={`${INPUT} tnum`}
                />
                <p className="text-xs text-faint">Up to two decimal places, e.g. 2499.50.</p>
                {amountTouched && amountError ? (
                  <p id={amountErrorId} role="alert" className="text-xs font-medium text-danger">
                    {amountError}
                  </p>
                ) : null}
              </div>

              <fieldset className="flex flex-col gap-2">
                <legend className="pb-1 text-sm font-semibold text-ink-soft">Billing interval</legend>
                <div className="grid grid-cols-2 gap-2">
                  <button
                    type="button"
                    aria-pressed={billingInterval === "month"}
                    onClick={() => setBillingInterval("month")}
                    className={toggleClass(billingInterval === "month")}
                  >
                    Monthly
                  </button>
                  <button
                    type="button"
                    aria-pressed={billingInterval === "year"}
                    onClick={() => setBillingInterval("year")}
                    className={toggleClass(billingInterval === "year")}
                  >
                    Yearly
                  </button>
                </div>
              </fieldset>

              <div className="flex flex-col gap-1.5">
                <label htmlFor={reasonId} className="text-sm font-semibold text-ink-soft">
                  Reason (required)
                </label>
                <input
                  id={reasonId}
                  required
                  maxLength={500}
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  onBlur={() => setReasonTouched(true)}
                  placeholder="e.g. Negotiated during the renewal call"
                  aria-invalid={reasonTouched && reasonError ? true : undefined}
                  aria-describedby={reasonTouched && reasonError ? reasonErrorId : undefined}
                  className={INPUT}
                />
                {reasonTouched && reasonError ? (
                  <p id={reasonErrorId} role="alert" className="text-xs font-medium text-danger">
                    {reasonError}
                  </p>
                ) : null}
              </div>

              <div className="flex flex-col gap-1.5">
                <label className="inline-flex items-start gap-2.5 text-sm font-semibold text-ink-soft">
                  <input
                    type="checkbox"
                    checked={hasSubscription && applyToSubscription}
                    disabled={!hasSubscription}
                    onChange={(e) => setApplyToSubscription(e.target.checked)}
                    className="mt-0.5 size-4 accent-[var(--color-accent)]"
                  />
                  Apply to their current subscription
                </label>
                {!hasSubscription ? (
                  <p className="pl-6 text-xs text-muted">
                    {org.name} has no subscription for {product.product_name} yet — this price is recorded
                    now and applies when they subscribe.
                  </p>
                ) : null}
              </div>

              <details className="rounded-tile bg-ground px-3 text-caption leading-relaxed text-muted">
                <summary className="hit-44 flex cursor-pointer items-center font-semibold text-ink-soft">
                  Details
                </summary>
                <p className="pb-3">
                  POSTs to <span className="mono">/api/v1/admin/organizations/{"{id}"}/pricing</span> —
                  clones {product.product_name}&apos;s public flat plan into a version pinned to {org.name}.
                </p>
              </details>
            </>
          ) : (
            <>
              <div className="flex flex-col gap-1 rounded-tile bg-ground px-4 py-3">
                <p className="text-sm font-semibold text-ink">
                  {parsedAmount.ok
                    ? summarySentence(product.product_name, org.name, parsedAmount.minor, "INR", billingInterval, predicted)
                    : null}
                </p>
                <p className="text-xs text-muted">Reason: {reasonTrimmed}</p>
              </div>
              <p className="text-xs text-faint">
                Expected outcome: {outcomePhrase(predicted)} — the confirmation after submitting
                reflects what actually happened. This creates a new price version; past prices and
                invoices are never edited.
              </p>
            </>
          )}
        </form>
      )}
    </Dialog>
  );
}

/* -------------------------------- the card -------------------------------- */

export function ClientPricingCard({
  org,
  refreshSignal = 0,
}: {
  org: PricingOrgRef;
  /** Bumped by a sibling (Add product) when the account's subscriptions changed. Only ever while
   *  that sibling's dialog has closed, so this card's own dialog cannot be open at the time. */
  refreshSignal?: number;
}) {
  const [reloadKey, setReloadKey] = useState(0);
  const [dialogProduct, setDialogProduct] = useState<ProductPricing | null>(null);
  const { hasRole } = useAuth();
  // A UI hint only — the server is what actually refuses the write, to a caller who isn't `owner`
  // or `billing_ops` (`services/console/app/api/routes.py`'s `OperatorBilling` guard). `support`
  // can read pricing but not set it, same as live.
  const canWritePricing = hasRole("owner") || hasRole("billing_ops");

  const loader = useCallback(() => getOrganizationPricing(org.organization_id), [org.organization_id]);
  const pricing = useAsync(loader, [org.organization_id, reloadKey, refreshSignal]);

  const products = useMemo(() => {
    if (pricing.status !== "success") return [];
    return [...pricing.data.products].sort((a, b) => a.product_name.localeCompare(b.product_name));
  }, [pricing]);

  const refresh = useCallback(() => setReloadKey((k) => k + 1), []);

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
      key: "subscription",
      header: "Subscription",
      render: (p) => <SubscriptionCell subscription={p.subscription} />,
    },
    {
      key: "current",
      header: "Current price",
      align: "right",
      render: (p) => <CurrentPriceCell price={p.current_price} />,
    },
    {
      key: "public",
      header: "List price",
      align: "right",
      cellClassName: "hidden sm:table-cell",
      headerClassName: "hidden sm:table-cell",
      render: (p) => <PublicPriceCell price={p.public_price} />,
    },
    {
      key: "actions",
      header: "",
      align: "right",
      render: (p) =>
        canWritePricing ? (
          <Button size="sm" variant="outline" onClick={() => setDialogProduct(p)}>
            Set price
          </Button>
        ) : null,
    },
  ];

  return (
    <Card id="pricing" className="flex scroll-mt-6 flex-col gap-4 p-6">
      <SectionHeader
        title="Client pricing"
        subtitle="Agreed per client — the customer app itself never shows a price."
      />

      {pricing.status === "loading" ? (
        <div className="flex flex-col gap-2">
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
        </div>
      ) : pricing.status === "error" ? (
        <ErrorState message={pricing.error.message} onRetry={refresh} />
      ) : products.length === 0 ? (
        <EmptyState
          title="No products to price"
          description="This organization's catalogue has no products yet."
        />
      ) : (
        <>
          <DataTable
            columns={columns}
            rows={products}
            rowKey={(p) => p.product_key}
            caption="Per-product pricing for this organization"
          />
          <div className="flex flex-col gap-3">
            {products.map((p) => (
              <ProductHistory key={p.product_key} product={p} />
            ))}
          </div>
        </>
      )}

      {dialogProduct ? (
        <SetPriceDialog
          org={org}
          product={dialogProduct}
          onClose={() => setDialogProduct(null)}
          onSuccess={refresh}
        />
      ) : null}
    </Card>
  );
}
