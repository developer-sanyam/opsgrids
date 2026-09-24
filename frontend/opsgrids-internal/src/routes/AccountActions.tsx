import { useId, useState } from "react";
import type { ReactNode } from "react";

import { postOverride } from "@/api/client";
import type { OrganizationRow, OverrideRequest, OverrideResult } from "@/api/types";
import { Button, Money } from "@/ui";
import { CheckCircleIcon, GiftIcon, PauseIcon, PlayIcon } from "@/ui/icons";
import { Modal } from "@/ui/Modal";

/**
 * Which account-detail action modal is open, or none. `"bespoke"` and `"migrate"` moved to the
 * "Client pricing" section's own "Set price" flow (`AccountPricing.tsx`) — see that module for the
 * per-product price-setting dialog the catalog's `bespoke_price` override still backs from Plans.tsx.
 */
export type ActionKind = null | "suspend" | "reactivate" | "comp";

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

