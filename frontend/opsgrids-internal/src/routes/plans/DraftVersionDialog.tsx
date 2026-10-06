import { useState } from "react";

import { Button, FormDialog, InlineAlert, Select, TextArea, TextField } from "@opsgrids/ui";

import { createVersion } from "@/api/catalog";
import type { BillingIntervalWire, PlanVersionOut, PriceKind } from "@/api/catalog";
import {
  SELLABLE_CURRENCIES,
  exponentOf,
  formatMicros,
  formatMinor,
  parseAmountToMinor,
  parseRateToMicros,
} from "@/lib/minorUnits";
import { buildVersionRequest, draftFromVersion, newPriceRow } from "./draftSheet";
import type { PriceDraft, SheetDraft, SheetErrors } from "./draftSheet";
import { ProblemList } from "./ProblemList";
import { useCatalogWrite } from "./useCatalogWrite";

const KIND_OPTIONS = [
  { value: "flat", label: "Flat" },
  { value: "per_seat", label: "Per seat" },
  { value: "usage", label: "Usage (metered)" },
];

const INTERVAL_OPTIONS = [
  { value: "month", label: "Monthly" },
  { value: "year", label: "Yearly" },
  { value: "one_time", label: "One-time" },
];

const CURRENCY_OPTIONS = SELLABLE_CURRENCIES.map((code) => ({ value: code, label: code }));

interface DraftVersionDialogProps {
  planId: string;
  planName: string;
  /** The version to start from — its whole sheet is copied — or null for an empty sheet. */
  source: PlanVersionOut | null;
  onClose: () => void;
  onDone: (created: PlanVersionOut) => void;
}

function PriceRow({
  row,
  index,
  currency,
  errors,
  onChange,
  onRemove,
}: {
  row: PriceDraft;
  index: number;
  currency: string;
  errors: SheetErrors;
  onChange: (next: PriceDraft) => void;
  onRemove: () => void;
}) {
  const at = `prices.${index}`;
  const digits = exponentOf(currency);
  // Read back exactly what will be sent, so a misread separator or exponent is visible before
  // the draft exists rather than only on the sheet afterwards.
  const rate = row.rate.trim() === "" ? null : parseRateToMicros(row.rate);
  const amount = row.amount.trim() === "" ? null : parseAmountToMinor(row.amount, currency);
  const rateHelper =
    rate?.ok === true ? `= ${formatMicros(rate.value, currency)} per unit` : "Up to 6 decimal places, e.g. 0.0025.";
  const amountHelper =
    amount?.ok === true
      ? `= ${formatMinor(amount.value, currency)}`
      : digits === 0
        ? "Whole units only."
        : `Up to ${digits} decimal places.`;
  return (
    <fieldset className="flex flex-col gap-3 rounded-card border border-line p-4">
      <legend className="px-1 text-sm font-semibold text-ink">Price {index + 1}</legend>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Select
          label="Kind"
          options={KIND_OPTIONS}
          value={row.kind}
          onChange={(event) => {
            const kind = event.target.value as PriceKind;
            onChange({ ...row, kind, unit: kind === "per_seat" && row.unit === "" ? "seat" : row.unit });
          }}
        />
        {row.kind === "usage" ? (
          <TextField
            label={`Rate per unit (${currency})`}
            required
            inputMode="decimal"
            helper={rateHelper}
            value={row.rate}
            onChange={(event) => onChange({ ...row, rate: event.target.value })}
            error={errors[`${at}.rate`]}
          />
        ) : (
          <TextField
            label={`Amount (${currency})`}
            required
            inputMode={digits === 0 ? "numeric" : "decimal"}
            helper={amountHelper}
            value={row.amount}
            onChange={(event) => onChange({ ...row, amount: event.target.value })}
            error={errors[`${at}.amount`]}
          />
        )}
        {row.kind !== "flat" ? (
          <TextField
            label="Unit"
            required={row.kind === "per_seat"}
            optional={row.kind === "usage"}
            placeholder={row.kind === "per_seat" ? "seat" : "email"}
            value={row.unit}
            onChange={(event) => onChange({ ...row, unit: event.target.value })}
            error={errors[`${at}.unit`]}
          />
        ) : null}
        {row.kind === "usage" ? (
          <TextField
            label="Meter key"
            required
            placeholder="emails_sent"
            value={row.meterKey}
            onChange={(event) => onChange({ ...row, meterKey: event.target.value })}
            error={errors[`${at}.meterKey`]}
          />
        ) : null}
      </div>
      <div>
        <Button type="button" variant="ghost" size="sm" onClick={onRemove}>
          Remove price {index + 1}
        </Button>
      </div>
    </fieldset>
  );
}

/**
 * Draft a new version from a full sheet. Prefilled from `source` — every price, every feature and
 * every seat class — and always sent whole, because catalog does not merge a partial body.
 * Drafting changes nothing anybody is billed; publishing (a separate, confirmed step) freezes it.
 */
export function DraftVersionDialog({ planId, planName, source, onClose, onDone }: DraftVersionDialogProps) {
  const [draft, setDraft] = useState<SheetDraft>(() => draftFromVersion(source));
  const [reason, setReason] = useState("");
  const [errors, setErrors] = useState<SheetErrors>({});
  const write = useCatalogWrite();

  function update(patch: Partial<SheetDraft>) {
    setDraft((current) => ({ ...current, ...patch }));
  }

  function updatePrice(index: number, next: PriceDraft) {
    setDraft((current) => ({
      ...current,
      prices: current.prices.map((row, i) => (i === index ? next : row)),
    }));
  }

  async function handleSubmit() {
    const built = buildVersionRequest(draft, reason);
    if (!built.ok) {
      setErrors(built.errors);
      return;
    }
    setErrors({});
    const created = await write.run((key) => createVersion(planId, built.request, key));
    if (created !== undefined) onDone(created);
  }

  const seatErrors = Object.entries(errors).filter(([field]) => field.startsWith("seatClasses."));
  const errorCount = Object.keys(errors).length;

  return (
    <FormDialog
      open
      size="lg"
      onClose={onClose}
      onSubmit={() => void handleSubmit()}
      title={`New draft — ${planName}`}
      description={
        source
          ? `Starts as a copy of version ${source.version_no}. Drafting changes nothing anyone is billed; publishing is a separate step.`
          : "An empty sheet. Drafting changes nothing anyone is billed; publishing is a separate step."
      }
      submitLabel="Create draft"
      pendingLabel="Creating draft…"
      pending={write.pending}
      error={write.error ?? (errorCount > 0 ? `Fix the ${errorCount === 1 ? "field" : "fields"} marked below.` : undefined)}
      reference={write.reference}
      dirty
    >
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Select
          label="Currency"
          options={CURRENCY_OPTIONS}
          value={draft.currency}
          onChange={(event) => update({ currency: event.target.value })}
          helper="Amounts are re-read in this currency's own minor unit."
        />
        <Select
          label="Billing interval"
          options={INTERVAL_OPTIONS}
          value={draft.billingInterval}
          onChange={(event) => update({ billingInterval: event.target.value as BillingIntervalWire })}
        />
      </div>
      <TextField
        label="Restricted to organization"
        optional
        helper="An organization id makes this a bespoke (audience-of-one) price. Leave empty for a public version."
        value={draft.restrictedToOrgId}
        onChange={(event) => update({ restrictedToOrgId: event.target.value })}
        error={errors.restrictedToOrgId}
      />

      <section aria-label="Prices" className="flex flex-col gap-3">
        <h3 className="text-sm font-semibold text-ink">Prices</h3>
        {errors.prices ? <InlineAlert tone="danger" title={errors.prices} live="off" /> : null}
        {draft.prices.map((row, index) => (
          <PriceRow
            key={row.key}
            row={row}
            index={index}
            currency={draft.currency}
            errors={errors}
            onChange={(next) => updatePrice(index, next)}
            onRemove={() => update({ prices: draft.prices.filter((_, i) => i !== index) })}
          />
        ))}
        <div>
          <Button type="button" variant="secondary" size="sm" onClick={() => update({ prices: [...draft.prices, newPriceRow()] })}>
            Add price
          </Button>
        </div>
      </section>

      <section aria-label="Features" className="flex flex-col gap-3">
        <h3 className="text-sm font-semibold text-ink">Features</h3>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <TextField
            label="Trial length (days)"
            optional
            inputMode="numeric"
            helper="Whole days, 0 to 365. 0 sells no trial; empty uses the platform's 14."
            value={draft.trialDays}
            onChange={(event) => update({ trialDays: event.target.value })}
            error={errors.trialDays}
          />
          <TextField
            label="Bundled products"
            optional
            helper="Bundle plans only: product keys, comma-separated (hrms, crm)."
            value={draft.bundledProducts}
            onChange={(event) => update({ bundledProducts: event.target.value })}
            error={errors.bundledProducts}
          />
        </div>
        {draft.otherFeatures.length > 0 ? (
          <ul aria-label="Other features, carried over" className="flex flex-col gap-2 text-sm">
            {draft.otherFeatures.map((feature) => (
              <li key={feature.feature_key} className="flex flex-wrap items-center justify-between gap-2">
                <span>
                  <span className="mono text-ink">{feature.feature_key}</span>
                  <span className="ml-2 text-muted">{JSON.stringify(feature.value)}</span>
                </span>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={() =>
                    update({ otherFeatures: draft.otherFeatures.filter((f) => f.feature_key !== feature.feature_key) })
                  }
                >
                  Remove {feature.feature_key}
                </Button>
              </li>
            ))}
          </ul>
        ) : null}
      </section>

      {draft.seatClasses.length > 0 ? (
        <section aria-label="Seat classes" className="flex flex-col gap-2">
          <h3 className="text-sm font-semibold text-ink">Seat classes (carried over)</h3>
          <ul className="flex flex-col gap-1 text-sm text-muted">
            {draft.seatClasses.map((seat) => {
              const index = draft.prices.findIndex((p) => p.sourceId === seat.price_id);
              return (
                <li key={seat.code}>
                  <span className="font-medium text-ink">{seat.name}</span>{" "}
                  {seat.price_id === null ? "— free" : index >= 0 ? `— charged at price ${index + 1}` : "— its price was removed"}
                </li>
              );
            })}
          </ul>
          {seatErrors.map(([field, message]) => (
            <InlineAlert key={field} tone="danger" title={message} live="off" />
          ))}
        </section>
      ) : null}

      <TextArea
        label="Reason"
        required
        rows={3}
        maxLength={500}
        helper="Kept on the audit record with your name."
        value={reason}
        onChange={(event) => setReason(event.target.value)}
        error={errors.reason}
      />
      <ProblemList problems={write.fieldErrors} />
    </FormDialog>
  );
}
