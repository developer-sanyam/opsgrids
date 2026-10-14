import { useRef, useState } from "react";

import { recordPurchase } from "@/api/client";
import type { DocumentType, PurchaseDocument, PurchaseDocumentCreate } from "@/api/types";
import { parseMinor } from "@/lib/money";
import { ApiError, problemMessage, useMutation } from "@opsgrids/client";
import { FormDialog, Select, TextArea, TextField, useFormDirty } from "@/ui";

const GSTIN_PATTERN = /^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$/;
const TAX_RATES = ["0", "0.25", "3", "5", "12", "18", "28"];
const TYPE_OPTIONS: { value: DocumentType | "debit_note"; label: string }[] = [
  { value: "invoice", label: "Invoice" },
  { value: "credit_note", label: "Credit note" },
];

interface PurchaseFormDialogProps {
  onClose: () => void;
  onSaved: (purchase: PurchaseDocument) => void;
}

/**
 * Record what a supplier billed — `POST /finance/purchases`. No update or delete path: a
 * supplier's own correction is recorded as their credit or debit note, never an edit to this one.
 * Tax heads are computed server-side from `taxable_minor` and `tax_rate_pct` — never submitted.
 */
export function PurchaseFormDialog({ onClose, onSaved }: PurchaseFormDialogProps) {
  const [supplierGstin, setSupplierGstin] = useState("");
  const [supplierName, setSupplierName] = useState("");
  const [number, setNumber] = useState("");
  const [documentType, setDocumentType] = useState<DocumentType>("invoice");
  const [documentDate, setDocumentDate] = useState("");
  const [taxable, setTaxable] = useState("");
  const [taxRatePct, setTaxRatePct] = useState("18");
  const [notes, setNotes] = useState("");

  const fields = { supplierGstin, supplierName, number, documentType, documentDate, taxable, taxRatePct, notes };
  const [initial] = useState(fields);
  const dirty = useFormDirty(initial, fields);

  const [gstinError, setGstinError] = useState<string | undefined>(undefined);
  const [numberError, setNumberError] = useState<string | undefined>(undefined);
  const [dateError, setDateError] = useState<string | undefined>(undefined);
  const [taxableError, setTaxableError] = useState<string | undefined>(undefined);
  const gstinRef = useRef<HTMLInputElement>(null);

  const save = useMutation((input: PurchaseDocumentCreate) => recordPurchase(input), {
    onSuccess: (purchase) => onSaved(purchase),
  });

  function submit() {
    setGstinError(undefined);
    setNumberError(undefined);
    setDateError(undefined);
    setTaxableError(undefined);

    const cleanedGstin = supplierGstin.trim().toUpperCase();
    if (!GSTIN_PATTERN.test(cleanedGstin)) {
      setGstinError("Enter a valid 15-character GSTIN.");
      gstinRef.current?.focus();
      return;
    }
    if (number.trim() === "") {
      setNumberError("Enter the supplier's document number.");
      return;
    }
    if (documentDate === "") {
      setDateError("Enter the document date.");
      return;
    }
    const parsed = parseMinor(taxable);
    if (!parsed.ok || parsed.minor <= 0) {
      setTaxableError(parsed.ok ? "Enter a taxable value greater than zero." : parsed.message);
      return;
    }

    void save.mutate({
      supplier_gstin: cleanedGstin,
      supplier_name: supplierName.trim(),
      number: number.trim(),
      document_type: documentType,
      document_date: documentDate,
      taxable_minor: parsed.minor,
      tax_rate_pct: Number(taxRatePct),
      notes: notes.trim() === "" ? null : notes.trim(),
    });
  }

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title="Record purchase"
      submitLabel="Record purchase"
      pendingLabel="Recording…"
      pending={save.pending}
      initialFocusRef={gstinRef}
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
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <TextField
          ref={gstinRef}
          label="Supplier GSTIN"
          value={supplierGstin}
          onChange={(e) => setSupplierGstin(e.target.value.toUpperCase())}
          maxLength={15}
          required
          error={gstinError ?? save.fieldErrors.supplier_gstin}
        />
        <TextField
          label="Supplier name"
          value={supplierName}
          onChange={(e) => setSupplierName(e.target.value)}
          optional
          error={save.fieldErrors.supplier_name}
        />
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <TextField
          label="Document number"
          value={number}
          onChange={(e) => setNumber(e.target.value)}
          required
          error={numberError ?? save.fieldErrors.number}
        />
        <Select
          label="Document type"
          value={documentType}
          onChange={(e) => setDocumentType(e.target.value as DocumentType)}
          options={TYPE_OPTIONS}
          error={save.fieldErrors.document_type}
        />
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <TextField
          label="Document date"
          type="date"
          value={documentDate}
          onChange={(e) => setDocumentDate(e.target.value)}
          required
          error={dateError ?? save.fieldErrors.document_date}
        />
        <Select
          label="Tax rate"
          value={taxRatePct}
          onChange={(e) => setTaxRatePct(e.target.value)}
          options={TAX_RATES.map((r) => ({ value: r, label: `${r}%` }))}
          error={save.fieldErrors.tax_rate_pct}
        />
      </div>

      <TextField
        label="Taxable value (₹)"
        inputMode="decimal"
        value={taxable}
        onChange={(e) => setTaxable(e.target.value)}
        helper="CGST/SGST/IGST are computed from this — never entered directly."
        required
        error={taxableError ?? save.fieldErrors.taxable_minor}
      />

      <TextArea label="Notes" value={notes} onChange={(e) => setNotes(e.target.value)} optional rows={2} />
    </FormDialog>
  );
}
