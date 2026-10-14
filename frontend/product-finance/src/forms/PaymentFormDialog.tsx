import { useMemo, useRef, useState } from "react";

import { listInvoices, MAX_PAGE_SIZE, recordPayment } from "@/api/client";
import type { Invoice, Payment, PaymentAllocationCreate, PaymentCreate, PaymentMethod } from "@/api/types";
import { outstandingMinor } from "@/data/derive";
import { formatMoney, minorToInput, parseMinor } from "@/lib/money";
import { today as realToday } from "@/lib/format";
import { ApiError, fieldError, problemMessage, useAsync, useMutation } from "@opsgrids/client";
import { Card, ErrorState, FormDialog, Select, Skeleton, TextArea, TextField, useFormDirty } from "@/ui";

const METHODS: { value: PaymentMethod; label: string }[] = [
  { value: "bank_transfer", label: "Bank transfer" },
  { value: "upi", label: "UPI" },
  { value: "card", label: "Card" },
  { value: "cheque", label: "Cheque" },
  { value: "cash", label: "Cash" },
];

interface PaymentFormDialogProps {
  /**
   * When set, the payment is recorded against exactly this invoice — the customer and the
   * allocation table are both fixed to it (the "Record payment" affordance on an invoice detail).
   * When omitted, a customer must be chosen first and every one of their open invoices is offered.
   */
  invoice?: Invoice;
  /** Only used when `invoice` is not set — a customer already chosen elsewhere (the /payments picker). */
  customers?: { value: string; label: string }[];
  onClose: () => void;
  onSaved: (payment: Payment) => void;
}

/**
 * Record a payment received and allocate it to invoices — `POST /finance/payments`. Append-only:
 * there is no edit, delete or "unapply" for a payment once it lands.
 */
export function PaymentFormDialog({ invoice, customers, onClose, onSaved }: PaymentFormDialogProps) {
  const [customerId, setCustomerId] = useState(invoice?.customer_id ?? "");
  const [reference, setReference] = useState("");
  const [receivedDate, setReceivedDate] = useState(realToday());
  const [method, setMethod] = useState<PaymentMethod>("bank_transfer");
  const [note, setNote] = useState("");
  const [amounts, setAmounts] = useState<Record<string, string>>(() =>
    invoice ? { [invoice.id]: minorToInput(outstandingMinor(invoice)) } : {},
  );

  const fields = { customerId, reference, receivedDate, method, note, amounts };
  const [initial] = useState(fields);
  const dirty = useFormDirty(initial, fields);

  const [referenceError, setReferenceError] = useState<string | undefined>(undefined);
  const [dateError, setDateError] = useState<string | undefined>(undefined);
  const [allocationError, setAllocationError] = useState<string | undefined>(undefined);
  const referenceRef = useRef<HTMLInputElement>(null);

  const openInvoices = useAsync(
    () =>
      invoice || customerId === ""
        ? Promise.resolve(invoice ? [invoice] : [])
        : listInvoices({ customerId, size: MAX_PAGE_SIZE }).then((page) =>
            page.items.filter((inv) => inv.document_type === "invoice" && (inv.status === "sent" || inv.status === "overdue")),
          ),
    [invoice, customerId],
  );

  const save = useMutation((input: PaymentCreate) => recordPayment(input), {
    onSuccess: (payment) => onSaved(payment),
  });
  const saveProblem = save.error instanceof ApiError ? save.error.problem : null;

  const rows = useMemo(() => (openInvoices.status === "success" ? openInvoices.data : []), [openInvoices]);

  function setAmount(invoiceId: string, value: string) {
    setAmounts((current) => ({ ...current, [invoiceId]: value }));
  }

  function submit() {
    setReferenceError(undefined);
    setDateError(undefined);
    setAllocationError(undefined);

    if (reference.trim() === "") {
      setReferenceError("Enter the bank UTR, cheque number or gateway reference.");
      referenceRef.current?.focus();
      return;
    }
    if (receivedDate === "") {
      setDateError("Enter the date the payment was received.");
      return;
    }
    if (receivedDate > realToday()) {
      setDateError("The received date can't be in the future.");
      return;
    }
    if (customerId === "") {
      setAllocationError("Choose the customer this payment is from.");
      return;
    }

    const allocations: PaymentAllocationCreate[] = [];
    for (const inv of rows) {
      const raw = amounts[inv.id];
      if (raw === undefined || raw.trim() === "") continue;
      const parsed = parseMinor(raw);
      if (!parsed.ok) {
        setAllocationError(`${inv.number}: ${parsed.message}`);
        return;
      }
      if (parsed.minor <= 0) continue;
      if (parsed.minor > outstandingMinor(inv)) {
        setAllocationError(`${inv.number}: allocation exceeds the outstanding ${formatMoney(outstandingMinor(inv))}.`);
        return;
      }
      allocations.push({ invoice_id: inv.id, amount_minor: parsed.minor });
    }
    if (allocations.length === 0) {
      setAllocationError("Allocate at least one amount to an invoice.");
      return;
    }

    void save.mutate({
      reference: reference.trim(),
      customer_id: customerId,
      received_date: receivedDate,
      method,
      allocations,
      note: note.trim() === "" ? null : note.trim(),
    });
  }

  const total = Object.values(amounts).reduce((sum, raw) => {
    const parsed = parseMinor(raw || "0");
    return sum + (parsed.ok ? parsed.minor : 0);
  }, 0);

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title={invoice ? `Record payment for ${invoice.number}` : "Record payment"}
      submitLabel="Record payment"
      pendingLabel="Recording…"
      pending={save.pending}
      initialFocusRef={referenceRef}
      size="lg"
      dirty={dirty}
      {...(save.error && Object.keys(save.fieldErrors).length === 0
        ? {
            error: problemMessage(save.error),
            ...(save.error instanceof ApiError && save.error.problem?.request_id
              ? { reference: save.error.problem.request_id }
              : {}),
          }
        : {})}
    >
      {!invoice ? (
        <Select
          label="Customer"
          value={customerId}
          onChange={(e) => setCustomerId(e.target.value)}
          placeholder="Choose a customer"
          options={customers ?? []}
          required
          error={allocationError ?? fieldError(saveProblem, "customer_id")}
        />
      ) : null}

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <TextField
          ref={referenceRef}
          label="Reference"
          value={reference}
          onChange={(e) => setReference(e.target.value)}
          helper="Bank UTR, cheque number or gateway reference."
          required
          error={referenceError ?? fieldError(saveProblem, "reference")}
        />
        <TextField
          label="Received date"
          type="date"
          value={receivedDate}
          onChange={(e) => setReceivedDate(e.target.value)}
          required
          error={dateError ?? fieldError(saveProblem, "received_date")}
        />
      </div>

      <Select
        label="Method"
        value={method}
        onChange={(e) => setMethod(e.target.value as PaymentMethod)}
        options={METHODS}
        error={fieldError(saveProblem, "method")}
      />

      <div className="flex flex-col gap-2">
        <p className="text-xs font-semibold uppercase tracking-wide text-faint">Allocate to</p>
        {customerId === "" && !invoice ? (
          <p className="text-sm text-muted">Choose a customer to see their open invoices.</p>
        ) : null}
        {openInvoices.status === "loading" ? <Skeleton className="h-24 w-full rounded-tile" /> : null}
        {openInvoices.status === "error" ? (
          <ErrorState message={problemMessage(openInvoices.error)} onRetry={openInvoices.reload} />
        ) : null}
        {openInvoices.status === "success" && rows.length === 0 && customerId !== "" ? (
          <p className="text-sm text-muted">This customer has no open invoices.</p>
        ) : null}
        {rows.length > 0 ? (
          <Card className="flex flex-col divide-y divide-line/70 p-2">
            {rows.map((inv) => (
              <div key={inv.id} className="flex items-center justify-between gap-3 px-2 py-2">
                <div className="flex flex-col leading-tight">
                  <span className="text-sm font-semibold text-ink tnum">{inv.number}</span>
                  <span className="text-xs text-muted">Outstanding {formatMoney(outstandingMinor(inv))}</span>
                </div>
                <div className="w-36">
                  <TextField
                    label={`Amount for ${inv.number} (₹)`}
                    inputMode="decimal"
                    value={amounts[inv.id] ?? ""}
                    onChange={(e) => setAmount(inv.id, e.target.value)}
                  />
                </div>
              </div>
            ))}
          </Card>
        ) : null}
        {allocationError ? <p className="text-sm font-medium text-negative">{allocationError}</p> : null}
        {rows.length > 0 ? (
          <p className="text-right text-sm font-semibold text-ink">Total allocated: {formatMoney(total)}</p>
        ) : null}
      </div>

      <TextArea label="Note" value={note} onChange={(e) => setNote(e.target.value)} optional rows={2} />
    </FormDialog>
  );
}
