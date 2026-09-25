import { useId, useState } from "react";
import type { ReactNode } from "react";

import { getOrganizationPricing, postOverride } from "@/api/client";
import type { OverrideRequest, OverrideResult, PricingOrgRef, ProductPricing } from "@/api/types";
import { useAsync } from "@/lib/useAsync";
import { AccountStatusPill, Button, ErrorState, Money, Skeleton } from "@/ui";
import { CheckCircleIcon, GiftIcon, PauseIcon, PlayIcon } from "@/ui/icons";
import { Modal } from "@/ui/Modal";

/** Which account-detail action modal is open, or none. */
export type ActionKind = null | "suspend" | "reactivate" | "comp";

interface ActionProps {
  org: PricingOrgRef;
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
    <p role="alert" className="rounded-tile bg-danger-soft px-3 py-2.5 text-sm font-medium text-danger">
      {message}
    </p>
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
  const reasonId = useId();
  const [reason, setReason] = useState("");
  const [subscriptionId, setSubscriptionId] = useState<string>("");
  const { submitting, error, result, run } = useOverride(onDone);

  const pricing = useAsync(() => getOrganizationPricing(org.organization_id), [org.organization_id]);
  const subscribed: ProductPricing[] =
    pricing.status === "success" ? pricing.data.products.filter((p) => p.subscription !== null) : [];
  const chosen = subscriptionId || subscribed[0]?.subscription?.id || "";

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
            if (!chosen) return;
            void run({
              action: "suspend",
              organization_id: org.organization_id,
              subscription_id: chosen,
              reason: reason.trim(),
            });
          }}
        >
          {error ? <ErrorNote message={error} /> : null}

          {pricing.status === "loading" ? (
            <Skeleton className="h-24 w-full" />
          ) : pricing.status === "error" ? (
            <ErrorState message={pricing.error.message} />
          ) : subscribed.length === 0 ? (
            <p className="rounded-tile bg-ground px-3 py-2.5 text-sm text-muted">
              {org.name} has no subscription on any product — there is nothing to suspend.
            </p>
          ) : (
            <fieldset className="flex flex-col gap-2">
              <legend className="pb-1 text-sm font-semibold text-ink-soft">Which subscription?</legend>
              {subscribed.map((p) => (
                <label
                  key={p.product_key}
                  className="flex items-center gap-2.5 rounded-tile border border-line px-3 py-2.5 text-sm has-[:checked]:border-accent has-[:checked]:bg-accent-soft"
                >
                  <input
                    type="radio"
                    name="subscription"
                    value={p.subscription?.id}
                    checked={chosen === p.subscription?.id}
                    onChange={() => setSubscriptionId(p.subscription?.id ?? "")}
                    className="size-4 accent-[var(--color-accent)]"
                  />
                  <span className="font-semibold text-ink">{p.product_name}</span>
                  <AccountStatusPill status={p.subscription?.state ?? "unknown"} />
                </label>
              ))}
            </fieldset>
          )}

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
            POSTs <span className="mono">{"{action: \"suspend\"}"}</span> to{" "}
            <span className="mono">/api/v1/admin/overrides</span>, which delegates to
            subscription-service's <span className="mono">/internal</span> suspend. The activation is
            projected off, and the gateway then answers the customer with a 402.
          </DelegationNote>
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={onClose} type="button">
              Cancel
            </Button>
            <Button
              variant="danger"
              type="submit"
              disabled={submitting || reason.trim().length === 0 || !chosen}
            >
              {submitting ? "Suspending…" : "Suspend account"}
            </Button>
          </div>
        </form>
      )}
    </Modal>
  );
}

/* ------------------------------ reactivate ------------------------------- */

/** There is no `reactivate` override on the live API — `delegation.py` resolves exactly `suspend`
 *  and `comp_grant`; anything else is a 400. Rather than ship a form that can only ever fail, this
 *  says so plainly. */
export function ReactivateAction({ org, onClose }: Omit<ActionProps, "onDone">) {
  return (
    <Modal
      open
      onClose={onClose}
      title={`Reactivate ${org.name}`}
      description="Restores entitlement; the customer regains access on the next token refresh."
      icon={<PlayIcon className="size-5" />}
    >
      <div className="flex flex-col gap-4">
        <p className="rounded-tile bg-ground px-3 py-2.5 text-sm leading-relaxed text-muted">
          Reactivating isn't available from the console yet. `/admin/overrides` only resolves
          <span className="mono"> suspend</span> and <span className="mono"> comp_grant</span> —
          there is no reactivate action on the live API to delegate this to.
        </p>
        <div className="flex justify-end">
          <Button variant="outline" onClick={onClose} type="button">
            Close
          </Button>
        </div>
      </div>
    </Modal>
  );
}

/* ------------------------------ comp grant ------------------------------- */

/** `POST /admin/overrides` for `comp_grant` accepts only `organization_id`, `amount_minor`,
 *  `currency` and `reason` — there is no `is_comp` flag and no `valid_until`, so this form no
 *  longer offers either: showing a control the server silently drops would be worse than not
 *  showing it (see `types.ts`). */
export function CompGrantAction({ org, onClose, onDone }: ActionProps) {
  const amountId = useId();
  const reasonId = useId();
  const [amount, setAmount] = useState("");
  const [reason, setReason] = useState("");
  const { submitting, error, result, run } = useOverride(onDone);

  const amountMinor = Math.round(Number(amount) * 100);
  const valid = amount !== "" && amountMinor > 0 && reason.trim().length > 0;

  return (
    <Modal
      open
      onClose={onClose}
      title={`Grant credit — ${org.name}`}
      description="An append-only credit-ledger entry. Nothing is mutated."
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
              action: "comp_grant",
              organization_id: org.organization_id,
              amount_minor: amountMinor,
              currency: "INR",
              reason: reason.trim(),
            });
          }}
        >
          {error ? <ErrorNote message={error} /> : null}

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
              Grants <Money minor={amountMinor} currency="INR" className="font-semibold text-ink" /> of
              credit, with no expiry — the live ledger has no expiry field on a grant made this way.
            </p>
          ) : null}

          <DelegationNote>
            POSTs <span className="mono">{"{action: \"comp_grant\"}"}</span> to{" "}
            <span className="mono">/api/v1/admin/overrides</span> → billing-service writes one
            append-only credit-ledger row.
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
