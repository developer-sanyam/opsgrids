import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { getInvoice, getSupplierSettings, issueInvoice, listCustomers, MAX_PAGE_SIZE } from "@/api/client";
import type { Customer, Invoice, InvoiceLineCreate, InvoiceIssueIn, SupplyType } from "@/api/types";
import { GST_STATE_NAME, GST_STATE_OPTIONS, gstStateName, stateCodeForName } from "@/data/gstStates";
import { isIntraState, previewInvoice } from "@/invoice/gstMath";
import type { PreviewLine } from "@/invoice/gstMath";
import { InvoiceTotals } from "@/invoice/InvoiceTotals";
import { LineEditor } from "@/invoice/LineEditor";
import { blankLine, newLineId } from "@/invoice/lineDraft";
import type { LineDraft } from "@/invoice/lineDraft";
import { SupplierSetupPrompt } from "@/invoice/SupplierSetupPrompt";
import { FORTY_PERCENT_FROM } from "@/invoice/vocabulary";
import { addDays, DEFAULT_PAYMENT_TERMS_DAYS } from "@/lib/format";
import { formatMoney, minorToInput, parseMinor, parseQuantity } from "@/lib/money";
import { ApiError, fieldError, problemMessage, useAsync, useCan, useEntitlement, useMutation } from "@opsgrids/client";
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

const SUPPLY_TYPES: { value: SupplyType | ""; label: string }[] = [
  { value: "", label: "Derived from the buyer's GSTIN" },
  { value: "b2b", label: "B2B — registered buyer" },
  { value: "b2c", label: "B2C — unregistered buyer" },
  { value: "export", label: "Export" },
  { value: "sez", label: "SEZ supply" },
];

const HSN_PATTERN = /^(\d{4}|\d{6}|\d{8})$/;

/** A line parsed for the preview, or null while any of its numbers is not yet a number. */
function parsePreviewLine(line: LineDraft): PreviewLine | null {
  const qty = parseQuantity(line.quantity);
  const unit = parseMinor(line.unitPrice || "0");
  const discount = line.discount.trim() === "" ? { ok: true as const, minor: 0 } : parseMinor(line.discount);
  if (!qty.ok || !unit.ok || !discount.ok) return null;
  return { quantity: qty.value, unitPriceMinor: unit.minor, discountMinor: Math.abs(discount.minor), ratePct: Number(line.ratePct) };
}

/** Where the supply is taxed, by the server's own order: the buyer's GSTIN, their billing state,
 *  the state name on file, then the supplier's own. */
function derivedPlaceOfSupply(customer: Customer | null, sellerState: string | null): string | null {
  if (customer === null) return null;
  if (customer.gstin) return customer.gstin.slice(0, 2);
  if (customer.state_code) return customer.state_code;
  return stateCodeForName(customer.state) ?? sellerState;
}

function billingAddress(customer: Customer): string {
  const cityLine = [customer.city, customer.pincode].filter((s) => s && s.trim() !== "").join(" ");
  return [customer.address_line1, customer.address_line2, cityLine]
    .map((s) => (s ?? "").trim())
    .filter(Boolean)
    .join(", ");
}

export function InvoiceNewPage() {
  const navigate = useNavigate();
  const toast = useToast();
  const [searchParams] = useSearchParams();
  const correctsId = searchParams.get("corrects");
  const isCreditNote = correctsId !== null;
  const canConfigure = useCan("finance.compliance.registration.write", "ORG");
  const hasOperations = useEntitlement("operations");
  const canReadItems = useCan("operations.inventory.item.read");

  const correcting = useAsync(
    useCallback(() => (correctsId ? getInvoice(correctsId) : Promise.resolve(null)), [correctsId]),
    [correctsId],
  );
  const customersState = useAsync(() => listCustomers({ sort: "name_asc", size: MAX_PAGE_SIZE }), []);
  // The supplier settings decide whether an invoice may be started at all. Reading them needs the
  // registration read; an issuer without it gets a 403, which leaves the decision to the server
  // and the printed copy's own checks rather than blocking a person who cannot see why.
  const supplier = useAsync(async () => {
    try {
      return await getSupplierSettings();
    } catch (error) {
      if (error instanceof ApiError && error.status === 403) return null;
      throw error;
    }
  }, []);

  const loading = customersState.status === "loading" || supplier.status === "loading" || (correctsId !== null && correcting.status === "loading");
  const failure = [customersState, supplier, ...(correctsId ? [correcting] : [])].find((s) => s.status === "error");

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader eyebrow="Accounts receivable" title={isCreditNote ? "Issue credit note" : "Issue invoice"} />
      {loading ? <Skeleton className="h-64 w-full rounded-tile" /> : null}
      {failure && failure.status === "error" ? (
        <Card className="p-6">
          <ErrorState
            message={problemMessage(failure.error)}
            onRetry={() => {
              customersState.reload();
              supplier.reload();
              correcting.reload();
            }}
          />
        </Card>
      ) : null}
      {supplier.status === "success" && supplier.data !== null && !supplier.data.ready ? (
        <SupplierSetupPrompt missing={supplier.data.missing} canConfigure={canConfigure} />
      ) : null}
      {!loading &&
      !failure &&
      customersState.status === "success" &&
      supplier.status === "success" &&
      (supplier.data === null || supplier.data.ready) &&
      (!correctsId || correcting.status === "success") ? (
        <IssueForm
          correcting={correcting.status === "success" ? correcting.data : null}
          customers={customersState.data.items.map((row) => row.customer)}
          sellerState={supplier.data?.registration?.state_code ?? null}
          canPickItems={hasOperations && canReadItems}
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

/** The server's field name for each draft key, so an edit clears the error shown on that field. */
const LINE_ERROR_FIELD: Record<string, string> = {
  hsnSac: "hsn_sac",
  unitPrice: "unit_price_minor",
  discount: "discount_minor",
  ratePct: "tax_rate_pct",
};

function linesFrom(correcting: Invoice | null): LineDraft[] {
  if (!correcting) return [blankLine()];
  return correcting.lines.map((l) => ({
    id: newLineId(),
    description: `Return — ${l.description}`,
    hsnSac: l.hsn_sac ?? "",
    quantity: String(-Math.abs(l.quantity)),
    unit: l.unit ?? "",
    unitPrice: minorToInput(l.unit_price_minor),
    discount: l.discount_minor ? minorToInput(l.discount_minor) : "",
    ratePct: String(l.tax_rate_pct ?? correcting.tax_rate_pct),
    itemId: l.item_id ?? null,
    itemSku: l.item_sku ?? null,
  }));
}

function IssueForm({
  correcting,
  customers,
  sellerState,
  canPickItems,
  onIssued,
  onCancel,
}: {
  correcting: Invoice | null;
  customers: Customer[];
  sellerState: string | null;
  canPickItems: boolean;
  onIssued: (invoice: Invoice) => void;
  onCancel: () => void;
}) {
  const isCreditNote = correcting !== null;
  const [customerId, setCustomerId] = useState(correcting?.customer_id ?? "");
  const today = new Date().toISOString().slice(0, 10);
  const [issuedDate, setIssuedDate] = useState(today);
  // Offered as issue date + 30 days (a credit note settles nothing, so it is due at once) until
  // the person picks a date of their own; after that the issue date no longer moves it.
  const [pickedDueDate, setPickedDueDate] = useState<string | null>(null);
  const defaultDueDate =
    isCreditNote || !/^\d{4}-\d{2}-\d{2}$/.test(issuedDate) ? issuedDate : addDays(issuedDate, DEFAULT_PAYMENT_TERMS_DAYS);
  const dueDate = pickedDueDate ?? defaultDueDate;
  const [supplyType, setSupplyType] = useState<SupplyType | "">(correcting?.supply_type ?? "");
  const [placeOfSupply, setPlaceOfSupply] = useState(correcting?.place_of_supply ?? "");
  const [reverseCharge, setReverseCharge] = useState(correcting?.reverse_charge ?? false);
  const [roundOff, setRoundOff] = useState(correcting ? (correcting.round_off_minor ?? 0) !== 0 : true);
  const [shipElsewhere, setShipElsewhere] = useState(false);
  const [shipTo, setShipTo] = useState("");
  const [notes, setNotes] = useState(correcting ? `Corrects ${correcting.number}.` : "");
  const [lines, setLines] = useState<LineDraft[]>(() => linesFrom(correcting));

  const fields = { customerId, issuedDate, dueDate, supplyType, placeOfSupply, reverseCharge, roundOff, shipTo, notes, lines };
  const [initial] = useState(fields);
  const changed = useFormDirty(initial, fields);

  const [customerError, setCustomerError] = useState<string | undefined>(undefined);
  const [dateError, setDateError] = useState<string | undefined>(undefined);
  const [linesError, setLinesError] = useState<string | undefined>(undefined);
  // Per-line, per-field problems from the last review, keyed `${line.id}.${field}`.
  const [lineErrors, setLineErrors] = useState<Record<string, string>>({});
  // Bumped when a review fails, so focus moves once the errors are on the page.
  const [focusTick, setFocusTick] = useState(0);
  const formRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (focusTick > 0) formRef.current?.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus();
  }, [focusTick]);
  const [confirming, setConfirming] = useState(false);
  // Set on a successful issue, then handed to `onIssued` from an effect — never from the same
  // handler as the save — so `UnsavedChangesGuard`'s blocker has already dropped before the
  // navigation it would otherwise catch and ask about.
  const [issued, setIssued] = useState<Invoice | null>(null);
  const dirty = changed && issued === null;

  const customer = customers.find((c) => c.id === customerId) ?? null;
  const effectivePos = placeOfSupply || derivedPlaceOfSupply(customer, sellerState);
  const effectiveSupply: SupplyType = supplyType || (customer?.gstin ? "b2b" : "b2c");
  const intraState = isIntraState(effectiveSupply, sellerState, effectivePos);

  const parsed = useMemo(() => lines.map(parsePreviewLine), [lines]);
  const preview = useMemo(
    () => previewInvoice(parsed.filter((l): l is PreviewLine => l !== null), { intraState, reverseCharge, roundOff }),
    [parsed, intraState, reverseCharge, roundOff],
  );
  const lineFigures = useMemo(() => {
    let k = 0;
    return parsed.map((p) => {
      if (p === null) return null;
      const line = preview.lines[k++];
      return line ? { taxable: line.taxable, tax: line.cgst + line.sgst + line.igst } : null;
    });
  }, [parsed, preview]);

  const issue = useMutation((input: InvoiceIssueIn) => issueInvoice(input), {
    onSuccess: (invoice) => setIssued(invoice),
  });
  const issueProblem: ProblemJson | null = issue.error instanceof ApiError ? issue.error.problem : null;

  useEffect(() => {
    if (issued) onIssued(issued);
  }, [issued, onIssued]);

  function updateLine(index: number, patch: Partial<LineDraft>) {
    const id = lines[index]?.id;
    if (id) {
      setLineErrors((current) => {
        const next = { ...current };
        for (const key of Object.keys(patch)) delete next[`${id}.${LINE_ERROR_FIELD[key] ?? key}`];
        return next;
      });
    }
    setLines((current) => current.map((l, i) => (i === index ? { ...l, ...patch } : l)));
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
    const problems: Record<string, string> = {};
    for (const [i, line] of lines.entries()) {
      const at = (field: string, message: string) => {
        problems[`${line.id}.${field}`] ??= message;
      };
      if (line.description.trim() === "") at("description", "Enter a description for this line.");
      if (line.hsnSac !== "" && !HSN_PATTERN.test(line.hsnSac)) at("hsn_sac", "An HSN or SAC code is 4, 6 or 8 numerals.");
      const qty = parseQuantity(line.quantity);
      if (!qty.ok) at("quantity", qty.message);
      else if (isCreditNote && qty.value > 0) at("quantity", "Enter a negative quantity: a credit note reduces what was billed.");
      else if (!isCreditNote && qty.value < 0) at("quantity", "A quantity can't be negative on a tax invoice.");
      const unit = parseMinor(line.unitPrice);
      if (!unit.ok) at("unit_price_minor", unit.message);
      const discount = line.discount.trim() === "" ? { ok: true as const, minor: 0 } : parseMinor(line.discount);
      if (!discount.ok || discount.minor < 0) at("discount_minor", "Enter the discount as a positive amount, or leave it blank.");
      else if (lineFigures[i] && discount.minor > Math.abs(preview.lines[i]?.gross ?? 0)) {
        at("discount_minor", "The discount is more than the line's value.");
      }
      if (line.ratePct === "40" && issuedDate < FORTY_PERCENT_FROM) {
        at("tax_rate_pct", "The 40% rate applies only to invoices dated from 22 September 2025.");
      }
      if (qty.ok && unit.ok && discount.ok) {
        parsedLines.push({
          description: line.description.trim(),
          quantity: qty.value,
          unit_price_minor: unit.minor,
          discount_minor: discount.minor,
          unit: line.unit === "" ? null : line.unit,
          hsn_sac: line.hsnSac === "" ? null : line.hsnSac,
          tax_rate_pct: Number(line.ratePct),
          item_id: line.itemId,
          item_sku: line.itemSku,
        });
      }
    }
    setLineErrors(problems);
    if (Object.keys(problems).length > 0) return null;

    if (isCreditNote && preview.total >= 0) {
      setLinesError("A credit note's total must be negative.");
      return null;
    }
    if (!isCreditNote && preview.total <= 0) {
      setLinesError("An invoice's total must be greater than zero.");
      return null;
    }

    return {
      customer_id: customerId,
      issued_date: issuedDate,
      due_date: dueDate,
      tax_rate_pct: parsedLines[0]?.tax_rate_pct ?? 18,
      lines: parsedLines,
      document_type: isCreditNote ? "credit_note" : "invoice",
      notes: notes.trim() === "" ? null : notes.trim(),
      corrects_invoice_id: correcting ? correcting.id : null,
      supply_type: supplyType === "" ? null : supplyType,
      place_of_supply: placeOfSupply === "" ? null : placeOfSupply,
      reverse_charge: reverseCharge,
      round_off: roundOff,
      ship_to_address: shipElsewhere && shipTo.trim() !== "" ? shipTo.trim() : null,
    };
  }

  function submit() {
    if (validate() === null) {
      setFocusTick((t) => t + 1);
      return;
    }
    setConfirming(true);
  }

  function confirmIssue() {
    const payload = validate();
    if (payload === null) return;
    void issue.mutate(payload);
  }

  const reviewLabel = isCreditNote ? "Review credit note" : "Review invoice";

  return (
    <>
      <UnsavedChangesGuard when={dirty} subject={isCreditNote ? "this credit note" : "this invoice"} />
      <div ref={formRef} className="grid grid-cols-1 items-start gap-6 lg:grid-cols-3">
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
              options={customers.map((c) => ({ value: c.id, label: c.name }))}
              disabled={isCreditNote}
              required
              error={customerError ?? fieldError(issueProblem, "customer_id")}
            />
            <div className="grid grid-cols-2 gap-4">
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
                onChange={(e) => setPickedDueDate(e.target.value)}
                required
                error={dateError ?? fieldError(issueProblem, "due_date")}
              />
            </div>
          </div>

          {customer ? (
            <BuyerSummary
              customer={customer}
              placeOfSupply={effectivePos}
              supplyType={effectiveSupply}
              intraState={intraState}
            />
          ) : null}

          <div className="flex flex-col gap-3">
            <SectionHeader title="Lines" subtitle="Tax is previewed here and computed by the server on issue" />
            {linesError ? <InlineAlert tone="danger" title={linesError} /> : null}
            {lines.map((line, i) => (
              <LineEditor
                key={line.id}
                index={i}
                line={line}
                figures={lineFigures[i] ?? null}
                canPickItems={canPickItems}
                canRemove={lines.length > 1}
                negativeQuantity={isCreditNote}
                onChange={(patch) => updateLine(i, patch)}
                onRemove={() => {
                  setLines((current) => (current.length > 1 ? current.filter((_, j) => j !== i) : current));
                  setLineErrors({});
                }}
                errorFor={(field) => lineErrors[`${line.id}.${field}`] ?? fieldError(issueProblem, `lines.${i}.${field}`)}
              />
            ))}
            <Button
              type="button"
              variant="outline"
              size="sm"
              leadingIcon={<PlusIcon className="size-4" />}
              onClick={() => setLines((current) => [...current, blankLine(current.at(-1)?.ratePct ?? "18")])}
              className="relative hit-44 self-start"
            >
              Add line
            </Button>
          </div>

          <div className="flex flex-col gap-3">
            <Switch
              checked={shipElsewhere}
              onChange={setShipElsewhere}
              label="Deliver to a different address"
              description="Printed as the delivery address when the goods go somewhere other than the billing address."
            />
            {shipElsewhere ? (
              <TextArea label="Delivery address" value={shipTo} onChange={(e) => setShipTo(e.target.value)} rows={3} maxLength={600} />
            ) : null}
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
                placeholder={effectivePos ? `Derived: ${effectivePos} — ${gstStateName(effectivePos)}` : "Derived from the buyer"}
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
            title="Totals"
            action={<SampleChip label="Preview" title="Computed here the way the server computes it; the issued invoice shows the server's figures" />}
          />
          <InvoiceTotals preview={preview} intraState={intraState} roundOff={roundOff} onRoundOffChange={setRoundOff} />
          <p className="text-xs text-muted">
            An issued invoice cannot be edited or deleted. A mistake is corrected with a credit note.
          </p>
          <div className="hidden flex-col gap-2 pt-2 md:flex">
            <Button onClick={submit} disabled={issue.pending}>
              {reviewLabel}
            </Button>
            <Button variant="ghost" onClick={onCancel} disabled={issue.pending}>
              Cancel
            </Button>
          </div>
          {issue.error && Object.keys(issue.fieldErrors).length === 0 ? (
            <InlineAlert
              tone="danger"
              title={problemMessage(issue.error)}
              {...(issue.error instanceof ApiError && issue.error.problem?.request_id ? { reference: issue.error.problem.request_id } : {})}
            />
          ) : null}
        </Card>
      </div>

      {/* Below md the review button lives in a bar that stays above the bottom navigation, with the
          total beside it: the long form never scrolls the one action out of reach. */}
      <StickyActionBar
        summary={
          <div className="flex min-w-0 flex-col leading-tight">
            <span className="text-caption text-muted">Total</span>
            <span className="money text-card-title font-bold text-ink">{formatMoney(preview.total)}</span>
          </div>
        }
      >
        <Button onClick={submit} disabled={issue.pending}>
          {reviewLabel}
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
            ? `This corrects ${correcting?.number} for ${formatMoney(preview.total)}. An issued document cannot be edited or deleted.`
            : `This bills ${customer?.name ?? "the customer"} ${formatMoney(preview.total)}. An issued invoice cannot be edited or deleted — a mistake is corrected with a credit note.`
        }
        confirmLabel={isCreditNote ? "Issue credit note" : "Issue invoice"}
        pendingLabel="Issuing…"
        pending={issue.pending}
        {...(issue.error ? { error: problemMessage(issue.error) } : {})}
      />
    </>
  );
}

/** The buyer facts the invoice will print, and what they decide: place of supply and tax heads. */
function BuyerSummary({
  customer,
  placeOfSupply,
  supplyType,
  intraState,
}: {
  customer: Customer;
  placeOfSupply: string | null;
  supplyType: SupplyType;
  intraState: boolean;
}) {
  const address = billingAddress(customer);
  return (
    <section aria-label="Buyer details" className="flex flex-col gap-2 rounded-tile bg-ground p-4 text-sm">
      <dl className="grid grid-cols-1 gap-x-6 gap-y-1 sm:grid-cols-2">
        <div className="flex gap-2">
          <dt className="text-muted">GSTIN</dt>
          <dd className="font-semibold text-ink tnum">{customer.gstin ?? "Unregistered (B2C)"}</dd>
        </div>
        <div className="flex gap-2">
          <dt className="text-muted">Place of supply</dt>
          <dd className="font-semibold text-ink">
            {placeOfSupply ? `${GST_STATE_NAME[placeOfSupply] ?? placeOfSupply} (${placeOfSupply})` : "Not known"}
          </dd>
        </div>
        <div className="flex gap-2 sm:col-span-2">
          <dt className="text-muted">Billing address</dt>
          <dd className="font-semibold text-ink">{address || "None on file"}</dd>
        </div>
        <div className="flex gap-2 sm:col-span-2">
          <dt className="text-muted">Tax</dt>
          <dd className="font-semibold text-ink">
            {supplyType.toUpperCase()} · {intraState ? "CGST + SGST (same state)" : "IGST (another state)"}
          </dd>
        </div>
      </dl>
      {address === "" ? (
        <p className="text-xs text-muted">
          Add the customer&rsquo;s billing address under Customers so it prints on the invoice. It is
          required for an unregistered buyer billed ₹50,000 or more.
        </p>
      ) : null}
    </section>
  );
}
