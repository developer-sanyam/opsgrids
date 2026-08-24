import { useId, useMemo, useState } from "react";
import type { ReactNode } from "react";

import { postOverride } from "@/api/client";
import type { CatalogBundle } from "@/api/client";
import type {
  OrganizationRow,
  OverrideRequest,
  OverrideResult,
  Subscription,
} from "@/api/types";
import { formatMoney } from "@/lib/money";
import { Button, Money } from "@/ui";
import { CheckCircleIcon, GiftIcon, PauseIcon, PlayIcon, RepeatIcon, TagIcon } from "@/ui/icons";
import { Modal } from "@/ui/Modal";

/** Which action modal is open, or none. */
export type ActionKind = null | "suspend" | "reactivate" | "comp" | "bespoke" | "migrate";

interface ActionProps {
  org: OrganizationRow;
  onClose: () => void;
  onDone: (result: OverrideResult) => void;
}

/* ------------------------- shared building blocks ------------------------- */

function FieldLabel({ htmlFor, children }: { htmlFor: string; children: ReactNode }) {
  return (
    <label htmlFor={htmlFor} className="text-sm font-semibold text-ink-soft">
      {children}
    </label>
  );
}

const INPUT =
  "h-11 rounded-tile bg-ground px-3 text-sm text-ink placeholder:text-faint focus:outline-none focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2";

/** The endpoint note every action shows, so it is clear what the form would POST to. */
function DelegationNote({ children }: { children: ReactNode }) {
  return (
    <p className="rounded-tile bg-ground px-3 py-2.5 text-xs leading-relaxed text-muted">{children}</p>
  );
}

function ErrorNote({ message }: { message: string }) {
  return (
    <p role="alert" className="rounded-tile bg-suspended-soft px-3 py-2.5 text-sm font-medium text-suspended">
      {message}
    </p>
  );
}

/** The success panel shown after an override is accepted. */
function ResultPanel({ result, onClose }: { result: OverrideResult; onClose: () => void }) {
  return (
    <div className="flex flex-col items-center gap-4 py-4 text-center">
      <span className="grid size-14 place-items-center rounded-full bg-active-soft text-active">
        <CheckCircleIcon className="size-7" />
      </span>
      <div className="flex flex-col gap-1">
        <p className="text-base font-bold">Override accepted</p>
        <p className="text-sm text-muted">{result.audit.summary}</p>
        <p className="mono text-xs text-faint">
          {result.override_id} · audit {result.audit.audit_id}
        </p>
      </div>
      <Button onClick={onClose} className="w-full">
        Done
      </Button>
    </div>
  );
}

/** A tiny submit hook so each form shares the submitting / error / result lifecycle. */
function useOverride(onDone: (r: OverrideResult) => void) {
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<OverrideResult | null>(null);

  async function run(request: OverrideRequest) {
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

export function SuspendAction({ org, onClose, onDone }: ActionProps) {
  const reasonId = useId();
  const [reason, setReason] = useState("");
  const { submitting, error, result, run } = useOverride(onDone);

  return (
    <Modal
      open
      onClose={onClose}
      title={`Suspend ${org.name}`}
      description="De-entitles the account on its next access-token refresh (≤ 15 min)."
      icon={<PauseIcon className="size-5" />}
    >
      {result ? (
        <ResultPanel result={result} onClose={onClose} />
      ) : (
        <form
          className="flex flex-col gap-4"
          onSubmit={(e) => {
            e.preventDefault();
            void run({ kind: "suspend", organization_id: org.organization_id, reason: reason.trim() });
          }}
        >
          {error ? <ErrorNote message={error} /> : null}
          <div className="flex flex-col gap-1.5">
            <FieldLabel htmlFor={reasonId}>Reason (required)</FieldLabel>
            <textarea
              id={reasonId}
              required
              rows={3}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="e.g. Non-payment after dunning window closed"
              className={`${INPUT} h-auto py-2.5`}
            />
          </div>
          <DelegationNote>
            POSTs <span className="mono">override.suspend</span> to{" "}
            <span className="mono">/api/v1/admin/overrides</span>, which delegates to
            subscription-service's <span className="mono">/internal</span> suspend. The activation is
            projected off, and the gateway then answers the customer with a 402.
          </DelegationNote>
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={onClose} type="button">
              Cancel
            </Button>
            <Button variant="danger" type="submit" disabled={submitting || reason.trim().length === 0}>
              {submitting ? "Suspending…" : "Suspend account"}
            </Button>
          </div>
        </form>
      )}
    </Modal>
  );
}

/* ------------------------------ reactivate ------------------------------- */

export function ReactivateAction({ org, onClose, onDone }: ActionProps) {
  const reasonId = useId();
  const [reason, setReason] = useState("");
  const { submitting, error, result, run } = useOverride(onDone);

  return (
    <Modal
      open
      onClose={onClose}
      title={`Reactivate ${org.name}`}
      description="Restores entitlement; the customer regains access on the next token refresh."
      icon={<PlayIcon className="size-5" />}
    >
      {result ? (
        <ResultPanel result={result} onClose={onClose} />
      ) : (
        <form
          className="flex flex-col gap-4"
          onSubmit={(e) => {
            e.preventDefault();
            void run({ kind: "reactivate", organization_id: org.organization_id, reason: reason.trim() });
          }}
        >
          {error ? <ErrorNote message={error} /> : null}
          <div className="flex flex-col gap-1.5">
            <FieldLabel htmlFor={reasonId}>Reason (required)</FieldLabel>
            <textarea
              id={reasonId}
              required
              rows={3}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="e.g. Payment recovered — restoring access"
              className={`${INPUT} h-auto py-2.5`}
            />
          </div>
          <DelegationNote>
            POSTs <span className="mono">override.reactivate</span> to{" "}
            <span className="mono">/api/v1/admin/overrides</span>; subscription-service moves the
            subscription back to <span className="mono">active</span> and re-projects the activation.
          </DelegationNote>
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={onClose} type="button">
              Cancel
            </Button>
            <Button type="submit" disabled={submitting || reason.trim().length === 0}>
              {submitting ? "Reactivating…" : "Reactivate"}
            </Button>
          </div>
        </form>
      )}
    </Modal>
  );
}

/* ------------------------------ comp grant ------------------------------- */

export function CompGrantAction({ org, onClose, onDone }: ActionProps) {
  const amountId = useId();
  const expiryId = useId();
  const reasonId = useId();
  const [amount, setAmount] = useState("");
  const [isComp, setIsComp] = useState(true);
  const [expiry, setExpiry] = useState("");
  const [reason, setReason] = useState("");
  const { submitting, error, result, run } = useOverride(onDone);

  const amountMinor = Math.round(Number(amount) * 100);
  const valid = amount !== "" && amountMinor > 0 && reason.trim().length > 0;

  return (
    <Modal
      open
      onClose={onClose}
      title={`Grant credit — ${org.name}`}
      description="An append-only credit-ledger entry. Nothing is mutated; balance is a SUM over entries."
      icon={<GiftIcon className="size-5" />}
    >
      {result ? (
        <ResultPanel result={result} onClose={onClose} />
      ) : (
        <form
          className="flex flex-col gap-4"
          onSubmit={(e) => {
            e.preventDefault();
            if (!valid) return;
            void run({
              kind: "comp_grant",
              organization_id: org.organization_id,
              amount_minor: amountMinor,
              currency: org.currency,
              is_comp: isComp,
              reason: reason.trim(),
              valid_until: expiry ? `${expiry}T23:59:59Z` : null,
            });
          }}
        >
          {error ? <ErrorNote message={error} /> : null}

          <fieldset className="flex flex-col gap-2">
            <legend className="pb-1 text-sm font-semibold text-ink-soft">Grant type</legend>
            <div className="grid grid-cols-2 gap-2">
              <button
                type="button"
                aria-pressed={isComp}
                onClick={() => setIsComp(true)}
                className={`rounded-tile border px-3 py-2.5 text-left text-sm font-semibold transition-colors ${
                  isComp ? "border-accent bg-accent-soft text-accent-ink" : "border-line text-muted hover:bg-ground"
                }`}
              >
                Comp access
                <span className="block text-xs font-normal text-muted">Complimentary use</span>
              </button>
              <button
                type="button"
                aria-pressed={!isComp}
                onClick={() => setIsComp(false)}
                className={`rounded-tile border px-3 py-2.5 text-left text-sm font-semibold transition-colors ${
                  !isComp ? "border-accent bg-accent-soft text-accent-ink" : "border-line text-muted hover:bg-ground"
                }`}
              >
                Monetary credit
                <span className="block text-xs font-normal text-muted">Offsets invoices</span>
              </button>
            </div>
          </fieldset>

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div className="flex flex-col gap-1.5">
              <FieldLabel htmlFor={amountId}>Amount (₹)</FieldLabel>
              <input
                id={amountId}
                type="number"
                min="1"
                step="1"
                required
                inputMode="numeric"
                value={amount}
                onChange={(e) => setAmount(e.target.value)}
                placeholder="45000"
                className={`${INPUT} tnum`}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <FieldLabel htmlFor={expiryId}>Expires (optional)</FieldLabel>
              <input
                id={expiryId}
                type="date"
                value={expiry}
                onChange={(e) => setExpiry(e.target.value)}
                className={`${INPUT} tnum`}
              />
            </div>
          </div>

          <div className="flex flex-col gap-1.5">
            <FieldLabel htmlFor={reasonId}>Reason (required)</FieldLabel>
            <input
              id={reasonId}
              required
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="e.g. 3 months complimentary — migration goodwill"
              className={INPUT}
            />
          </div>

          {amountMinor > 0 ? (
            <p className="text-sm text-muted">
              Grants{" "}
              <Money minor={amountMinor} currency={org.currency} className="font-semibold text-ink" />{" "}
              {isComp ? "of comp access" : "of credit"}
              {expiry ? `, lapsing ${expiry}.` : ", with no expiry."}
            </p>
          ) : null}

          <DelegationNote>
            POSTs <span className="mono">override.comp_grant</span> to{" "}
            <span className="mono">/api/v1/admin/overrides</span> → billing-service writes one
            append-only credit-ledger row. Revoking later is a reversing row, never a mutation.
          </DelegationNote>

          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={onClose} type="button">
              Cancel
            </Button>
            <Button type="submit" disabled={submitting || !valid}>
              {submitting ? "Granting…" : "Grant credit"}
            </Button>
          </div>
        </form>
      )}
    </Modal>
  );
}

/* ----------------------------- bespoke price ----------------------------- */

export function BespokePriceAction({
  org,
  catalog,
  onClose,
  onDone,
}: ActionProps & { catalog: CatalogBundle | null }) {
  const productId = useId();
  const planId = useId();
  const amountId = useId();
  const reasonId = useId();

  const products = catalog?.products ?? [];
  const [productKey, setProductKey] = useState(() => products[0]?.product_key ?? "");
  const plans = useMemo(
    () => (catalog?.plans ?? []).filter((p) => p.product_key === productKey),
    [catalog, productKey],
  );
  const [basePlanId, setBasePlanId] = useState(() => plans[0]?.plan_id ?? "");
  const [amount, setAmount] = useState("");
  const [reason, setReason] = useState("");
  const { submitting, error, result, run } = useOverride(onDone);

  const amountMinor = Math.round(Number(amount) * 100);
  const resolvedPlan = plans.find((p) => p.plan_id === basePlanId) ?? plans[0];
  const effectivePlanId = resolvedPlan?.plan_id ?? "";
  const valid = productKey !== "" && effectivePlanId !== "" && amountMinor > 0 && reason.trim().length > 0;

  return (
    <Modal
      open
      onClose={onClose}
      title={`Bespoke price — ${org.name}`}
      description="Clones the plan into a new audience-of-one version and pins this org's subscription to it."
      icon={<TagIcon className="size-5" />}
    >
      {result ? (
        <ResultPanel result={result} onClose={onClose} />
      ) : (
        <form
          className="flex flex-col gap-4"
          onSubmit={(e) => {
            e.preventDefault();
            if (!valid) return;
            void run({
              kind: "bespoke_price",
              organization_id: org.organization_id,
              product_key: productKey,
              base_plan_id: effectivePlanId,
              amount_minor: amountMinor,
              currency: org.currency,
              reason: reason.trim(),
            });
          }}
        >
          {error ? <ErrorNote message={error} /> : null}

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div className="flex flex-col gap-1.5">
              <FieldLabel htmlFor={productId}>Product</FieldLabel>
              <select
                id={productId}
                value={productKey}
                onChange={(e) => {
                  setProductKey(e.target.value);
                  setBasePlanId("");
                }}
                className={`${INPUT} cursor-pointer`}
              >
                {products.map((p) => (
                  <option key={p.product_key} value={p.product_key}>
                    {p.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="flex flex-col gap-1.5">
              <FieldLabel htmlFor={planId}>Base plan to clone</FieldLabel>
              <select
                id={planId}
                value={effectivePlanId}
                onChange={(e) => setBasePlanId(e.target.value)}
                className={`${INPUT} cursor-pointer`}
              >
                {plans.map((p) => (
                  <option key={p.plan_id} value={p.plan_id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </div>
          </div>

          <div className="flex flex-col gap-1.5">
            <FieldLabel htmlFor={amountId}>Bespoke monthly price (₹)</FieldLabel>
            <input
              id={amountId}
              type="number"
              min="1"
              step="1"
              required
              inputMode="numeric"
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
              placeholder="420000"
              className={`${INPUT} tnum`}
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <FieldLabel htmlFor={reasonId}>Reason (required)</FieldLabel>
            <input
              id={reasonId}
              required
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="e.g. Negotiated enterprise rate — 3-year commit"
              className={INPUT}
            />
          </div>

          {amountMinor > 0 ? (
            <p className="text-sm text-muted">
              Creates a version restricted to {org.name} at{" "}
              <Money minor={amountMinor} currency={org.currency} className="font-semibold text-ink" /> / mo.
              Every other subscription stays on its own version.
            </p>
          ) : null}

          <DelegationNote>
            POSTs <span className="mono">catalog.bespoke_price.created</span> to{" "}
            <span className="mono">/api/v1/admin/overrides</span> → catalog-service clones a new plan
            version with <span className="mono">restricted_to_org_id</span> set, then pins this
            org's subscription. Immutable versions mean past invoices stay reconstructable.
          </DelegationNote>

          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={onClose} type="button">
              Cancel
            </Button>
            <Button type="submit" disabled={submitting || !valid}>
              {submitting ? "Creating…" : "Create & pin price"}
            </Button>
          </div>
        </form>
      )}
    </Modal>
  );
}

/* ------------------------------ migrate plan ----------------------------- */

export function MigratePlanAction({
  org,
  subscriptions,
  catalog,
  onClose,
  onDone,
}: ActionProps & { subscriptions: Subscription[]; catalog: CatalogBundle | null }) {
  const subId = useId();
  const versionId = useId();
  const reasonId = useId();

  const migratable = subscriptions.filter((s) => s.state === "active" || s.state === "past_due");
  const [subscriptionId, setSubscriptionId] = useState(() => migratable[0]?.subscription_id ?? "");
  const selectedSub = migratable.find((s) => s.subscription_id === subscriptionId) ?? migratable[0];

  const targetVersions = useMemo(() => {
    if (!catalog || !selectedSub) return [];
    return catalog.versions.filter(
      (v) =>
        v.product_key === selectedSub.product_key &&
        v.state === "published" &&
        v.plan_version_id !== selectedSub.plan_version_id &&
        (v.restricted_to_org_id === null || v.restricted_to_org_id === org.organization_id),
    );
  }, [catalog, selectedSub, org.organization_id]);

  const [targetId, setTargetId] = useState("");
  const [reason, setReason] = useState("");
  const { submitting, error, result, run } = useOverride(onDone);

  const effectiveTarget = targetId || targetVersions[0]?.plan_version_id || "";
  const valid = subscriptionId !== "" && effectiveTarget !== "" && reason.trim().length > 0;

  return (
    <Modal
      open
      onClose={onClose}
      title={`Migrate plan — ${org.name}`}
      description="Pins the subscription to a new plan version. The old version is preserved for history."
      icon={<RepeatIcon className="size-5" />}
    >
      {result ? (
        <ResultPanel result={result} onClose={onClose} />
      ) : migratable.length === 0 ? (
        <div className="flex flex-col gap-4">
          <p className="text-sm text-muted">
            This account has no active subscription to migrate. Reactivate it first.
          </p>
          <div className="flex justify-end">
            <Button variant="outline" onClick={onClose}>
              Close
            </Button>
          </div>
        </div>
      ) : (
        <form
          className="flex flex-col gap-4"
          onSubmit={(e) => {
            e.preventDefault();
            if (!valid) return;
            void run({
              kind: "plan_migration",
              organization_id: org.organization_id,
              subscription_id: subscriptionId,
              target_plan_version_id: effectiveTarget,
              reason: reason.trim(),
            });
          }}
        >
          {error ? <ErrorNote message={error} /> : null}

          <div className="flex flex-col gap-1.5">
            <FieldLabel htmlFor={subId}>Subscription</FieldLabel>
            <select
              id={subId}
              value={subscriptionId}
              onChange={(e) => {
                setSubscriptionId(e.target.value);
                setTargetId("");
              }}
              className={`${INPUT} cursor-pointer`}
            >
              {migratable.map((s) => (
                <option key={s.subscription_id} value={s.subscription_id}>
                  {s.product_name} · {s.plan_name} (v{s.plan_version_no})
                </option>
              ))}
            </select>
          </div>

          <div className="flex flex-col gap-1.5">
            <FieldLabel htmlFor={versionId}>Target plan version</FieldLabel>
            {targetVersions.length === 0 ? (
              <p className="rounded-tile bg-ground px-3 py-2.5 text-sm text-muted">
                No other published version is available for this product.
              </p>
            ) : (
              <select
                id={versionId}
                value={effectiveTarget}
                onChange={(e) => setTargetId(e.target.value)}
                className={`${INPUT} cursor-pointer`}
              >
                {targetVersions.map((v) => (
                  <option key={v.plan_version_id} value={v.plan_version_id}>
                    {v.plan_name} · v{v.version_no} ·{" "}
                    {formatMoney(v.prices.find((p) => p.kind === "flat")?.amount_minor ?? 0, v.currency)}
                    /mo
                  </option>
                ))}
              </select>
            )}
          </div>

          <div className="flex flex-col gap-1.5">
            <FieldLabel htmlFor={reasonId}>Reason (required)</FieldLabel>
            <input
              id={reasonId}
              required
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="e.g. Upgrade to Scale at renewal"
              className={INPUT}
            />
          </div>

          <DelegationNote>
            POSTs <span className="mono">override.plan_migration</span> to{" "}
            <span className="mono">/api/v1/admin/overrides</span> → subscription-service previews the
            money delta and pins the new version. The prior pin is preserved so past invoices still
            reconstruct.
          </DelegationNote>

          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={onClose} type="button">
              Cancel
            </Button>
            <Button type="submit" disabled={submitting || !valid || targetVersions.length === 0}>
              {submitting ? "Migrating…" : "Migrate plan"}
            </Button>
          </div>
        </form>
      )}
    </Modal>
  );
}
