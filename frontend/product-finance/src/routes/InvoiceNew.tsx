import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { getInvoice, issueInvoice, listCustomers, MAX_PAGE_SIZE } from "@/api/client";
import type { Invoice, InvoiceLineCreate, InvoiceIssueIn, SupplyType } from "@/api/types";
import { GST_STATE_OPTIONS } from "@/data/gstStates";
import { formatMoney, minorToInput, parseMinor, parseQuantity } from "@/lib/money";
import { ApiError, fieldError, problemMessage, useAsync, useMutation } from "@opsgrids/client";
import {
  Button,
  Card,
  ConfirmDialog,
  ErrorState,
  InlineAlert,
  PageHeader,
  SampleChip,
  SectionHeader,
  Select,
  Skeleton,
  Switch,
  TextArea,
  TextField,
  UnsavedChangesGuard,
  useFormDirty,
} from "@/ui";
import { PlusIcon, StickyActionBar, useToast } from "@opsgrids/ui";
import type { ProblemJson } from "@opsgrids/client";

const TAX_RATES = ["0", "0.25", "3", "5", "12", "18", "28"];
const SUPPLY_TYPES: { value: SupplyType | ""; label: string }[] = [
  { value: "", label: "Derived from the buyer's GSTIN" },
  { value: "b2b", label: "B2B — registered buyer" },
  { value: "b2c", label: "B2C — unregistered buyer" },
  { value: "export", label: "Export" },
  { value: "sez", label: "SEZ supply" },
];

interface LineDraft {
  description: string;
  quantity: string;
  unitPrice: string;
  hsnSac: string;
  taxRateOverride: string;
}

function blankLine(): LineDraft {
  return { description: "", quantity: "1", unitPrice: "", hsnSac: "", taxRateOverride: "" };
}

/** A rough client-side preview only — the server computes and returns the real split. */
function previewTotal(lines: LineDraft[], defaultRatePct: number): { subtotal: number; tax: number } {
  let subtotal = 0;
  let tax = 0;
  for (const line of lines) {
    const qty = parseQuantity(line.quantity);
    const unit = parseMinor(line.unitPrice || "0");
    if (!qty.ok || !unit.ok) continue;
    const amount = Math.round(unit.minor * qty.value);
    const rate = line.taxRateOverride !== "" ? Number(line.taxRateOverride) : defaultRatePct;
    subtotal += amount;
    tax += Math.round((amount * rate) / 100);
  }
  return { subtotal, tax };
}

export function InvoiceNewPage() {
  const navigate = useNavigate();
  const toast = useToast();
  const [searchParams] = useSearchParams();
  const correctsId = searchParams.get("corrects");
  const isCreditNote = correctsId !== null;

  const correcting = useAsync(
    useCallback(() => (correctsId ? getInvoice(correctsId) : Promise.resolve(null)), [correctsId]),
    [correctsId],
  );
  const customersState = useAsync(() => listCustomers({ sort: "name_asc", size: MAX_PAGE_SIZE }), []);

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Accounts receivable"
        title={isCreditNote ? "Issue credit note" : "Issue invoice"}
      />
      {correctsId && correcting.status === "loading" ? <Skeleton className="h-64 w-full rounded-tile" /> : null}
      {correctsId && correcting.status === "error" ? (
        <Card className="p-6">
          <ErrorState message={problemMessage(correcting.error)} onRetry={correcting.reload} />
        </Card>
      ) : null}
      {customersState.status === "error" ? (
        <Card className="p-6">
          <ErrorState message={problemMessage(customersState.error)} onRetry={customersState.reload} />
        </Card>
      ) : null}
      {customersState.status === "loading" ? <Skeleton className="h-64 w-full rounded-tile" /> : null}
      {customersState.status === "success" && (!correctsId || correcting.status === "success") ? (
        <IssueForm
          correcting={correcting.status === "success" ? correcting.data : null}
          customers={customersState.data.items.map((row) => ({ value: row.customer.id, label: row.customer.name }))}
          onIssued={(invoice) => {
            toast.success(`${invoice.number} issued.`);
            navigate(`/invoices/${invoice.id}`);
          }}
          onCancel={() => navigate(-1)}
        />
      ) : null}
    </div>
  );
}

function IssueForm({
  correcting,
  customers,
  onIssued,
  onCancel,
}: {
  correcting: Invoice | null;
  customers: { value: string; label: string }[];
  onIssued: (invoice: Invoice) => void;
  onCancel: () => void;
}) {
  const isCreditNote = correcting !== null;
  const [customerId, setCustomerId] = useState(correcting?.customer_id ?? "");
  const today = new Date().toISOString().slice(0, 10);
  const [issuedDate, setIssuedDate] = useState(isCreditNote ? today : today);
  const [dueDate, setDueDate] = useState(isCreditNote ? today : today);
  const [taxRatePct, setTaxRatePct] = useState(String(correcting?.tax_rate_pct ?? 18));
  const [supplyType, setSupplyType] = useState<SupplyType | "">(correcting?.supply_type ?? "");
  const [placeOfSupply, setPlaceOfSupply] = useState(correcting?.place_of_supply ?? "");
  const [reverseCharge, setReverseCharge] = useState(correcting?.reverse_charge ?? false);
  const [notes, setNotes] = useState(correcting ? `Corrects ${correcting.number}.` : "");
  const [lines, setLines] = useState<LineDraft[]>(() =>
    correcting
      ? correcting.lines.map((l) => ({
          description: `Return — ${l.description}`,
          quantity: String(-Math.abs(l.quantity)),
          unitPrice: minorToInput(l.unit_price_minor),
          hsnSac: l.hsn_sac ?? "",
          taxRateOverride: l.tax_rate_pct !== undefined && l.tax_rate_pct !== correcting.tax_rate_pct ? String(l.tax_rate_pct) : "",
        }))
      : [blankLine()],
  );

  const fields = {
    customerId,
    issuedDate,
    dueDate,
    taxRatePct,
    supplyType,
    placeOfSupply,
    reverseCharge,
    notes,
    lines,
  };
  const [initial] = useState(fields);
  const changed = useFormDirty(initial, fields);

  const [customerError, setCustomerError] = useState<string | undefined>(undefined);
  const [dateError, setDateError] = useState<string | undefined>(undefined);
  const [linesError, setLinesError] = useState<string | undefined>(undefined);
  const [confirming, setConfirming] = useState(false);
  // Set on a successful issue, then handed to `onIssued` from an effect — never from the same
  // handler as the save — so `UnsavedChangesGuard`'s blocker has already dropped before the
  // navigation it would otherwise catch and ask about.
  const [issued, setIssued] = useState<Invoice | null>(null);
  const dirty = changed && issued === null;

  const rate = Number(taxRatePct);
  const preview = useMemo(() => previewTotal(lines, rate), [lines, rate]);
  const previewTotalMinor = preview.subtotal + preview.tax;

  const issue = useMutation((input: InvoiceIssueIn) => issueInvoice(input), {
    onSuccess: (invoice) => setIssued(invoice),
  });
  const issueProblem: ProblemJson | null = issue.error instanceof ApiError ? issue.error.problem : null;

  useEffect(() => {
    if (issued) onIssued(issued);
  }, [issued, onIssued]);

  function updateLine(index: number, patch: Partial<LineDraft>) {
    setLines((current) => current.map((l, i) => (i === index ? { ...l, ...patch } : l)));
  }

  function addLine() {
    setLines((current) => [...current, blankLine()]);
  }

  function removeLine(index: number) {
    setLines((current) => (current.length > 1 ? current.filter((_, i) => i !== index) : current));
  }

  function validate(): InvoiceIssueIn | null {
    setCustomerError(undefined);
    setDateError(undefined);
    setLinesError(undefined);

    if (customerId === "") {
      setCustomerError("Choose the customer being billed.");
      return null;
    }
    if (issuedDate === "" || dueDate === "") {
      setDateError("Both dates are required.");
      return null;
    }
    if (dueDate < issuedDate) {
      setDateError("The due date can't be before the issue date.");
      return null;
    }

    const parsedLines: InvoiceLineCreate[] = [];
    for (const line of lines) {
      if (line.description.trim() === "") {
        setLinesError("Every line needs a description.");
        return null;
      }
      const qty = parseQuantity(line.quantity);
      if (!qty.ok) {
        setLinesError(qty.message);
        return null;
      }
      if (isCreditNote && qty.value > 0) {
        setLinesError("A credit note's line quantities must be negative — it reduces what was billed.");
        return null;
      }
      if (!isCreditNote && qty.value < 0) {
        setLinesError("A quantity can't be negative on a tax invoice.");
        return null;
      }
      const unit = parseMinor(line.unitPrice);
      if (!unit.ok) {
        setLinesError(unit.message);
        return null;
      }
      parsedLines.push({
        description: line.description.trim(),
        quantity: qty.value,
        unit_price_minor: unit.minor,
        hsn_sac: line.hsnSac.trim() === "" ? null : line.hsnSac.trim(),
        tax_rate_pct: line.taxRateOverride === "" ? null : Number(line.taxRateOverride),
      });
    }

    if (isCreditNote && previewTotalMinor >= 0) {
      setLinesError("A credit note's total must be negative.");
      return null;
    }
    if (!isCreditNote && previewTotalMinor <= 0) {
      setLinesError("An invoice's total must be greater than zero.");
      return null;
    }

    return {
      customer_id: customerId,
      issued_date: issuedDate,
      due_date: dueDate,
      tax_rate_pct: rate,
      lines: parsedLines,
      document_type: isCreditNote ? "credit_note" : "invoice",
      notes: notes.trim() === "" ? null : notes.trim(),
      corrects_invoice_id: correcting ? correcting.id : null,
      supply_type: supplyType === "" ? null : supplyType,
      place_of_supply: placeOfSupply === "" ? null : placeOfSupply,
      reverse_charge: reverseCharge,
    };
  }

  function submit() {
    if (validate() === null) return;
    setConfirming(true);
  }

  function confirmIssue() {
    const payload = validate();
    if (payload === null) return;
    void issue.mutate(payload);
  }

  return (
    <>
      <UnsavedChangesGuard when={dirty} subject={isCreditNote ? "this credit note" : "this invoice"} />
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3 items-start">
        <Card className="flex flex-col gap-5 p-6 lg:col-span-2 lg:p-8">
          {correcting ? (
            <InlineAlert tone="info" title={`Correcting ${correcting.number}`}>
              This credit note links back to the original invoice. Lines are prefilled as returns
              — edit the quantities and amounts to match what is actually being credited.
            </InlineAlert>
          ) : null}

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Select
              label="Customer"
              value={customerId}
              onChange={(e) => setCustomerId(e.target.value)}
              placeholder="Choose a customer"
              options={customers}
              disabled={isCreditNote}
              required
              error={customerError ?? fieldError(issueProblem, "customer_id")}
            />
            <Select
              label="Tax rate"
              value={taxRatePct}
              onChange={(e) => setTaxRatePct(e.target.value)}
              options={TAX_RATES.map((r) => ({ value: r, label: `${r}%` }))}
              error={fieldError(issueProblem, "tax_rate_pct")}
            />
          </div>

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <TextField
              label="Issued date"
              type="date"
              value={issuedDate}
              onChange={(e) => setIssuedDate(e.target.value)}
              required
              error={dateError ? undefined : fieldError(issueProblem, "issued_date")}
            />
            <TextField
              label="Due date"
              type="date"
              value={dueDate}
              onChange={(e) => setDueDate(e.target.value)}
              required
              error={dateError ?? fieldError(issueProblem, "due_date")}
            />
          </div>

          <div className="flex flex-col gap-3">
            <SectionHeader title="Lines" subtitle="Server-computed tax — this is a preview only" />
            {linesError ? <InlineAlert tone="danger" title={linesError} /> : null}
            {lines.map((line, i) => (
              <div key={i} className="flex flex-col gap-3 rounded-tile border border-line p-4">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-xs font-semibold uppercase tracking-wide text-faint">Line {i + 1}</span>
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={() => removeLine(i)}
                    disabled={lines.length === 1}
                    aria-label={`Remove line ${i + 1}`}
                  >
                    Remove
                  </Button>
                </div>
                <div className="grid grid-cols-1 items-start gap-3 sm:grid-cols-3">
                  <div className="sm:col-span-3">
                    <TextField
                      label="Description"
                      value={line.description}
                      onChange={(e) => updateLine(i, { description: e.target.value })}
                      required
                      error={fieldError(issueProblem, `lines.${i}.description`)}
                    />
                  </div>
                  <TextField
                    label="Quantity"
                    inputMode="decimal"
                    value={line.quantity}
                    onChange={(e) => updateLine(i, { quantity: e.target.value })}
                    required
                  />
                  <TextField
                    label="Unit price (₹)"
                    inputMode="decimal"
                    value={line.unitPrice}
                    onChange={(e) => updateLine(i, { unitPrice: e.target.value })}
                    required
                  />
                  <TextField
                    label="HSN/SAC"
                    value={line.hsnSac}
                    onChange={(e) => updateLine(i, { hsnSac: e.target.value })}
                    optional
                  />
                </div>
              </div>
            ))}
            <Button type="button" variant="outline" size="sm" leadingIcon={<PlusIcon className="size-4" />} onClick={addLine} className="self-start">
              Add line
            </Button>
          </div>

          <details className="rounded-tile border border-line p-4">
            <summary className="relative hit-44 cursor-pointer text-sm font-bold text-ink">Advanced GST fields</summary>
            <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2">
              <Select
                label="Supply type"
                value={supplyType}
                onChange={(e) => setSupplyType(e.target.value as SupplyType | "")}
                options={SUPPLY_TYPES}
              />
              <Select
                label="Place of supply"
                value={placeOfSupply}
                onChange={(e) => setPlaceOfSupply(e.target.value)}
                placeholder="Derived from the buyer"
                options={[...GST_STATE_OPTIONS]}
              />
            </div>
            <div className="mt-4">
              <Switch
                checked={reverseCharge}
                onChange={setReverseCharge}
                label="Reverse charge"
                description="The recipient pays the tax instead of the supplier."
              />
            </div>
          </details>

          <TextArea label="Notes" value={notes} onChange={(e) => setNotes(e.target.value)} optional rows={2} />
        </Card>

        <Card className="flex flex-col gap-4 p-6">
          <SectionHeader
            title="Estimated total"
            action={<SampleChip label="Estimate" title="Client-side preview only — the server computes the real tax split" />}
          />
          <div className="flex flex-col gap-2 text-sm">
            <div className="flex justify-between">
              <span className="text-muted">Subtotal</span>
              <span className="money font-semibold text-ink">{formatMoney(preview.subtotal)}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted">Tax (est.)</span>
              <span className="money font-semibold text-ink">{formatMoney(preview.tax)}</span>
            </div>
            <div className="flex justify-between border-t border-line pt-2">
              <span className="font-bold text-ink">Total (est.)</span>
              <span className="money text-lg font-extrabold text-ink">{formatMoney(previewTotalMinor)}</span>
            </div>
          </div>
          <p className="text-xs text-muted">
            An issued invoice cannot be edited or deleted. A mistake is corrected with a credit
            note.
          </p>
          <div className="hidden flex-col gap-2 pt-2 md:flex">
            <Button onClick={submit} disabled={issue.pending}>
              {isCreditNote ? "Review credit note" : "Review invoice"}
            </Button>
            <Button variant="ghost" onClick={onCancel} disabled={issue.pending}>
              Cancel
            </Button>
          </div>
          {issue.error && Object.keys(issue.fieldErrors).length === 0 ? (
            <InlineAlert
              tone="danger"
              title={problemMessage(issue.error)}
              {...(issue.error instanceof ApiError && issue.error.problem?.request_id
                ? { reference: issue.error.problem.request_id }
                : {})}
            />
          ) : null}
        </Card>
      </div>

      {/* Below md the review button lives in a bar that stays above the bottom navigation, with the
          estimate beside it: the long form never scrolls the one action out of reach. */}
      <StickyActionBar
        summary={
          <div className="flex min-w-0 flex-col leading-tight">
            <span className="text-xs text-muted">Total (est.)</span>
            <span className="money text-base font-extrabold text-ink">{formatMoney(previewTotalMinor)}</span>
          </div>
        }
      >
        <Button onClick={submit} disabled={issue.pending}>
          {isCreditNote ? "Review credit note" : "Review invoice"}
        </Button>
      </StickyActionBar>

      <ConfirmDialog
        open={confirming}
        onClose={() => {
          setConfirming(false);
          issue.reset();
        }}
        onConfirm={confirmIssue}
        title={isCreditNote ? "Issue this credit note?" : "Issue this invoice?"}
        body={
          isCreditNote
            ? `This corrects ${correcting?.number} for an estimated ${formatMoney(previewTotalMinor)}. An issued document cannot be edited or deleted.`
            : `This bills the customer an estimated ${formatMoney(previewTotalMinor)}. An issued invoice cannot be edited or deleted — a mistake is corrected with a credit note.`
        }
        confirmLabel={isCreditNote ? "Issue credit note" : "Issue invoice"}
        pendingLabel="Issuing…"
        pending={issue.pending}
        {...(issue.error ? { error: problemMessage(issue.error) } : {})}
      />
    </>
  );
}
