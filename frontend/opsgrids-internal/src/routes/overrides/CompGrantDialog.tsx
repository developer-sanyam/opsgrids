import { useId, useRef, useState } from "react";

import { useMutation } from "@opsgrids/client";
import { FormDialog, RadioGroup, Select, TextArea, TextField } from "@opsgrids/ui";
import type { RadioOption } from "@opsgrids/ui";

import { postOverride } from "@/api/client";
import type { OrganizationRow, OverrideRequest, OverrideResult } from "@/api/types";
import { referenceOf } from "@/lib/errors";
import { parseMinorUnits } from "@/lib/money";
import { Money } from "@/ui";
import { Code, DelegationNote } from "./parts";
import { failureText } from "./text";

/** The request fields this dialog has a control for; a server error naming another is shown up top. */
const COMP_FIELDS = ["amount_minor", "valid_until", "reason"];

const GRANT_TYPES: RadioOption[] = [
  { value: "comp", label: "Comp access", description: "Complimentary use" },
  { value: "credit", label: "Monetary credit", description: "Offsets invoices" },
];

interface CompGrantDialogProps {
  /** The account this is opened from, or `null` on the Grants screen, where the customer is chosen. */
  org: OrganizationRow | null;
  /** The customers a grant can be recorded against. Ignored while `org` fixes the customer. */
  organizations: OrganizationRow[];
  onClose: () => void;
  onDone: (result: OverrideResult) => void;
}

/**
 * Record one append-only credit-ledger entry: complimentary access or a monetary credit, for an
 * amount, optionally lapsing on a date. Nothing is mutated — a balance is a SUM over entries, and
 * taking one back is a reversing row (`ReverseDialog`), never an edit.
 *
 * The amount is parsed to **integer minor units** from the digits typed, never through a float,
 * and the currency is the customer's own. Every rule here — a customer, an amount above zero, a
 * reason — is one the server enforces too; this only says so before the round trip.
 */
export function CompGrantDialog({ org, organizations, onClose, onDone }: CompGrantDialogProps) {
  const typeName = useId();
  const [customer, setCustomer] = useState(org?.organization_id ?? organizations[0]?.organization_id ?? "");
  const [grantType, setGrantType] = useState("comp");
  const [amount, setAmount] = useState("");
  const [expiry, setExpiry] = useState("");
  const [reason, setReason] = useState("");
  const [errors, setErrors] = useState<Partial<Record<"customer" | "amount" | "reason", string>>>({});
  const customerRef = useRef<HTMLSelectElement>(null);
  const amountRef = useRef<HTMLInputElement>(null);
  const reasonRef = useRef<HTMLTextAreaElement>(null);

  const target = org ?? organizations.find((o) => o.organization_id === customer) ?? null;
  const currency = target?.currency ?? "INR";
  const isComp = grantType === "comp";
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
    if (amountMinor === null) next.amount = "Enter an amount in rupees, like 45000.";
    else if (amountMinor <= 0) next.amount = "Enter an amount greater than zero.";
    if (reason.trim().length === 0) next.reason = "Give a reason — it goes into the audit log.";
    setErrors(next);
    // Focus goes to the first field that is wrong, in the order the form reads.
    const firstInvalid = next.customer
      ? customerRef
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
      kind: "comp_grant",
      organization_id: target.organization_id,
      amount_minor: amountMinor,
      currency,
      is_comp: isComp,
      reason: reason.trim(),
      valid_until: expiry ? `${expiry}T23:59:59Z` : null,
    });
  }

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title={fixed ? `Grant credit — ${org.name}` : "Grant credit or comp access"}
      description={
        fixed
          ? "An append-only credit-ledger entry. Nothing is mutated; balance is a SUM over entries."
          : "Writes one append-only credit-ledger entry. Balance stays a SUM; nothing is mutated."
      }
      submitLabel={fixed ? "Grant credit" : "Record grant"}
      pendingLabel={fixed ? "Granting…" : "Recording…"}
      pending={run.pending}
      initialFocusRef={fixed ? amountRef : customerRef}
      error={failureText(run.error, run.fieldErrors, fixed ? COMP_FIELDS : [...COMP_FIELDS, "organization_id"])}
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

      <RadioGroup
        legend="Grant type"
        name={typeName}
        value={grantType}
        onChange={setGrantType}
        options={GRANT_TYPES}
      />

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <TextField
          ref={amountRef}
          label="Amount (₹)"
          type="number"
          min="1"
          step="1"
          inputMode="numeric"
          value={amount}
          onChange={(event) => setAmount(event.target.value)}
          placeholder="45000"
          className="tnum"
          error={errors.amount ?? run.fieldErrors.amount_minor}
        />
        <TextField
          label="Expires"
          optional
          type="date"
          value={expiry}
          onChange={(event) => setExpiry(event.target.value)}
          className="tnum"
          error={run.fieldErrors.valid_until}
        />
      </div>

      <TextArea
        ref={reasonRef}
        label="Reason (required)"
        rows={2}
        value={reason}
        onChange={(event) => setReason(event.target.value)}
        placeholder="e.g. 3 months complimentary — migration goodwill"
        error={errors.reason ?? run.fieldErrors.reason}
      />

      {amountMinor !== null && amountMinor > 0 ? (
        <p className="text-sm text-muted" role="status">
          Grants <Money minor={amountMinor} currency={currency} className="font-semibold text-ink" />{" "}
          {isComp ? "of comp access" : "of credit"}
          {expiry ? `, lapsing ${expiry}.` : ", with no expiry."}
        </p>
      ) : null}

      {fixed ? (
        <DelegationNote>
          POSTs <Code>override.comp_grant</Code> to <Code>/api/v1/admin/overrides</Code> →
          billing-service writes one append-only credit-ledger row. Revoking later is a reversing
          row, never a mutation.
        </DelegationNote>
      ) : null}
    </FormDialog>
  );
}
