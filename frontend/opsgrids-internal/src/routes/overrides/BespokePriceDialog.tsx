import { useMemo, useRef, useState } from "react";

import { useMutation } from "@opsgrids/client";
import { FormDialog, Select, TextArea, TextField } from "@opsgrids/ui";

import { postOverride } from "@/api/client";
import type { CatalogBundle } from "@/api/client";
import type { OrganizationRow, OverrideRequest, OverrideResult } from "@/api/types";
import { referenceOf } from "@/lib/errors";
import { parseMinorUnits } from "@/lib/money";
import { Money } from "@/ui";
import { Code, DelegationNote } from "./parts";
import { failureText } from "./text";

/** The request fields this dialog has a control for; a server error naming another is shown up top. */
const BESPOKE_FIELDS = ["product_key", "base_plan_id", "amount_minor", "reason"];

interface BespokePriceDialogProps {
  /** The account this is opened from, or `null` on the Plans screen, where the customer is chosen. */
  org: OrganizationRow | null;
  organizations: OrganizationRow[];
  catalog: CatalogBundle;
  onClose: () => void;
  onDone: (result: OverrideResult) => void;
}

/**
 * Price one customer without re-pricing anyone: clone a plan into a new audience-of-one version,
 * restricted to that customer, and pin their subscription to it (ADR-0004 §2). Plan versions are
 * immutable and a price change is a new version, which is why this creates a version rather than
 * editing one — past invoices stay reconstructable.
 *
 * The price is parsed to integer minor units from the digits typed, never through a float.
 */
export function BespokePriceDialog({
  org,
  organizations,
  catalog,
  onClose,
  onDone,
}: BespokePriceDialogProps) {
  const [customer, setCustomer] = useState(org?.organization_id ?? organizations[0]?.organization_id ?? "");
  const [productKey, setProductKey] = useState(catalog.products[0]?.product_key ?? "");
  const [basePlanId, setBasePlanId] = useState("");
  const [amount, setAmount] = useState("");
  const [reason, setReason] = useState("");
  const [errors, setErrors] = useState<Partial<Record<"customer" | "plan" | "amount" | "reason", string>>>({});
  const customerRef = useRef<HTMLSelectElement>(null);
  const planRef = useRef<HTMLSelectElement>(null);
  const amountRef = useRef<HTMLInputElement>(null);
  const reasonRef = useRef<HTMLTextAreaElement>(null);

  const plans = useMemo(
    () => catalog.plans.filter((p) => p.product_key === productKey),
    [catalog.plans, productKey],
  );
  const effectivePlanId = (plans.find((p) => p.plan_id === basePlanId) ?? plans[0])?.plan_id ?? "";

  const target = org ?? organizations.find((o) => o.organization_id === customer) ?? null;
  const currency = target?.currency ?? "INR";
  const amountMinor = parseMinorUnits(amount, currency);
  const fixed = org !== null;

  const run = useMutation((request: OverrideRequest) => postOverride(request), {
    onSuccess: (result) => {
      onClose();
      onDone(result);
    },
  });

  function submit() {
    const next: typeof errors = {};
    if (target === null) next.customer = "Choose a customer.";
    if (effectivePlanId === "") next.plan = "Choose a plan to clone.";
    if (amountMinor === null) next.amount = "Enter a price in rupees, like 420000.";
    else if (amountMinor <= 0) next.amount = "Enter a price greater than zero.";
    if (reason.trim().length === 0) next.reason = "Give a reason — it goes into the audit log.";
    setErrors(next);
    const firstInvalid = next.customer
      ? customerRef
      : next.plan
        ? planRef
        : next.amount
          ? amountRef
          : next.reason
            ? reasonRef
            : null;
    if (firstInvalid !== null) {
      firstInvalid.current?.focus();
      return;
    }
    if (target === null || amountMinor === null) return;
    void run.mutate({
      kind: "bespoke_price",
      organization_id: target.organization_id,
      product_key: productKey,
      base_plan_id: effectivePlanId,
      amount_minor: amountMinor,
      currency,
      reason: reason.trim(),
    });
  }

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title={fixed ? `Bespoke price — ${org.name}` : "Create a bespoke price"}
      description={
        fixed
          ? "Clones the plan into a new audience-of-one version and pins this org's subscription to it."
          : "An audience-of-one plan version, pinned to a single customer. No shared plan is re-priced."
      }
      submitLabel={fixed ? "Create & pin price" : "Create bespoke price"}
      pendingLabel="Creating…"
      pending={run.pending}
      initialFocusRef={fixed ? planRef : customerRef}
      error={failureText(run.error, run.fieldErrors, fixed ? BESPOKE_FIELDS : [...BESPOKE_FIELDS, "organization_id"])}
      reference={referenceOf(run.error)}
    >
      {fixed ? null : (
        <Select
          ref={customerRef}
          label="Customer"
          options={organizations.map((o) => ({ value: o.organization_id, label: o.name }))}
          value={customer}
          onChange={(event) => setCustomer(event.target.value)}
          error={errors.customer ?? run.fieldErrors.organization_id}
        />
      )}

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Select
          label="Product"
          options={catalog.products.map((p) => ({ value: p.product_key, label: p.name }))}
          value={productKey}
          onChange={(event) => {
            setProductKey(event.target.value);
            setBasePlanId("");
          }}
          error={run.fieldErrors.product_key}
        />
        <Select
          ref={planRef}
          label="Base plan to clone"
          options={plans.map((p) => ({ value: p.plan_id, label: p.name }))}
          value={effectivePlanId}
          onChange={(event) => setBasePlanId(event.target.value)}
          error={errors.plan ?? run.fieldErrors.base_plan_id}
        />
      </div>

      <TextField
        ref={amountRef}
        label="Bespoke monthly price (₹)"
        type="number"
        min="1"
        step="1"
        inputMode="numeric"
        value={amount}
        onChange={(event) => setAmount(event.target.value)}
        placeholder="420000"
        className="tnum"
        error={errors.amount ?? run.fieldErrors.amount_minor}
      />

      <TextArea
        ref={reasonRef}
        label="Reason (required)"
        rows={2}
        value={reason}
        onChange={(event) => setReason(event.target.value)}
        placeholder="e.g. Negotiated enterprise rate — 3-year commit"
        error={errors.reason ?? run.fieldErrors.reason}
      />

      {amountMinor !== null && amountMinor > 0 && target ? (
        <p className="text-sm text-muted" role="status">
          {fixed ? `Creates a version restricted to ${target.name} at ` : `Pins ${target.name} to a new version at `}
          <Money minor={amountMinor} currency={currency} className="font-semibold text-ink" /> / mo.
          {fixed ? " Every other subscription stays on its own version." : ""}
        </p>
      ) : null}

      <DelegationNote>
        POSTs <Code>catalog.bespoke_price.created</Code> to <Code>/api/v1/admin/overrides</Code> →
        catalog-service clones a new plan version with <Code>restricted_to_org_id</Code> set, then
        pins {fixed ? "this org's" : "the"} subscription. Immutable versions mean past invoices
        stay reconstructable.
      </DelegationNote>
    </FormDialog>
  );
}
