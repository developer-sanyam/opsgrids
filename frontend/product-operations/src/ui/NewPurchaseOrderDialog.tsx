import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

import { createPurchaseOrder, listSuppliers } from "@/api/client";
import type { PurchaseOrderOut } from "@/api/types";
import { formatMoney } from "@/lib/format";
import { Button } from "@/ui";
import {
  CloseIcon,
  Combobox,
  Dialog,
  InlineAlert,
  PlusIcon,
  Select,
  TextArea,
  TextField,
  useAnnounce,
} from "@opsgrids/ui";
import type { ComboboxCopy, ComboboxOption, SelectOption } from "@opsgrids/ui";

import {
  buildPurchaseOrderPayload,
  emptyLine,
  emptyPurchaseOrder,
  hasErrors,
  lineAmountMinor,
  orderTotalMinor,
  validatePurchaseOrder,
} from "./purchaseOrderForm";
import type {
  CreatableStatus,
  LineDraft,
  LineField,
  PurchaseOrderErrors,
  PurchaseOrderFormValues,
} from "./purchaseOrderForm";

const STATUS_OPTIONS: SelectOption[] = [
  { value: "draft", label: "Draft" },
  { value: "approved", label: "Approved" },
];

/** The currencies `@/lib/format`'s `formatMoney` renders with a real symbol rather than a bare
 *  ISO code — picking from this list is what keeps every total this dialog shows legible
 *  everywhere else the PO is later rendered. */
const CURRENCY_OPTIONS: SelectOption[] = [
  { value: "INR", label: "INR" },
  { value: "USD", label: "USD" },
  { value: "EUR", label: "EUR" },
  { value: "GBP", label: "GBP" },
];

const SUPPLIER_COMBOBOX_COPY: Partial<ComboboxCopy> = {
  idle: "Start typing a supplier's name or code.",
  short: "Keep typing — at least two letters.",
  loading: "Searching suppliers…",
  noMatches: (query) => `No supplier matches "${query}".`,
  noMatchesHint: "Check the spelling, or try their supplier code.",
  error: "Couldn't load suppliers.",
  results: (count) => (count === 1 ? "1 supplier" : `${count} suppliers`),
};

interface NewPurchaseOrderDialogProps {
  open: boolean;
  onClose: () => void;
  /** Called once the server has accepted the order. The caller closes the dialog and refreshes
   *  its own list — this component's job ends at "created". */
  onCreated: (po: PurchaseOrderOut) => void;
}

/**
 * The "New purchase order" form.
 *
 * Money is collected in rupees and converted to integer minor units at the edge
 * (`purchaseOrderForm.ts`'s `parseRupeesToMinor`) — never a float in the middle. The live line
 * and order totals are computed the same way the server derives them
 * (`quantity * unit_price_minor`, summed), so what is shown here before submitting is what gets
 * stored.
 *
 * A rejected write never closes the dialog or clears what was typed — the server's own message
 * renders in an `InlineAlert` and the form stays exactly as the user left it.
 */
export function NewPurchaseOrderDialog({ open, onClose, onCreated }: NewPurchaseOrderDialogProps) {
  const [values, setValues] = useState<PurchaseOrderFormValues>(() =>
    emptyPurchaseOrder(crypto.randomUUID()),
  );
  const [errors, setErrors] = useState<PurchaseOrderErrors>({});
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const announce = useAnnounce();

  const numberContainerRef = useRef<HTMLDivElement>(null);
  const supplierContainerRef = useRef<HTMLDivElement>(null);
  const expectedDateContainerRef = useRef<HTMLDivElement>(null);
  const lineContainerRefs = useRef(new Map<string, HTMLDivElement>());
  const initialFocusRef = useRef<HTMLElement | null>(null);
  const suppliersRef = useRef<Promise<ComboboxOption[]> | null>(null);

  // Reset per-open, but never per-keystroke — nothing typed is lost while the dialog is up, and a
  // failed submit leaves the form exactly as the user left it (only re-opening starts fresh).
  useEffect(() => {
    if (!open) return;
    setValues(emptyPurchaseOrder(crypto.randomUUID()));
    setErrors({});
    setSubmitting(false);
    setFormError(null);
    suppliersRef.current = null;
  }, [open]);

  // A layout effect, not a passive one: `Dialog`'s own focus effect reads `initialFocusRef.current`
  // as soon as it mounts, and every layout effect in a commit fires before any passive effect.
  useLayoutEffect(() => {
    if (!open) return;
    initialFocusRef.current =
      numberContainerRef.current?.querySelector<HTMLElement>("input") ?? null;
  }, [open]);

  const loadSupplierOptions = useCallback(async (query: string): Promise<ComboboxOption[]> => {
    if (!suppliersRef.current) {
      suppliersRef.current = listSuppliers()
        .then((page) => page.items.map((s) => ({ id: s.code, label: s.name, sublabel: s.code })))
        .catch((err: unknown) => {
          // Cache only success — a failed load must not poison a retry with the same stale promise.
          suppliersRef.current = null;
          throw err;
        });
    }
    const all = await suppliersRef.current;
    const q = query.trim().toLowerCase();
    return all
      .filter(
        (option) => option.label.toLowerCase().includes(q) || (option.sublabel ?? "").toLowerCase().includes(q),
      )
      .slice(0, 20);
  }, []);

  function clearError(key: keyof PurchaseOrderErrors) {
    setErrors((current) => {
      if (current[key] === undefined) return current;
      const next = { ...current };
      delete next[key];
      return next;
    });
  }

  function updateLineField(key: string, field: Exclude<keyof LineDraft, "key">, value: string) {
    setValues((v) => ({
      ...v,
      lines: v.lines.map((line) => (line.key === key ? { ...line, [field]: value } : line)),
    }));
    if (field === "sku" || field === "quantity" || field === "unitPrice") {
      setErrors((current) => {
        const rowErrors = current.lineErrors?.[key];
        if (!rowErrors || rowErrors[field] === undefined) return current;
        const nextRow = { ...rowErrors };
        delete nextRow[field];
        const nextLineErrors = { ...current.lineErrors };
        if (Object.keys(nextRow).length > 0) nextLineErrors[key] = nextRow;
        else delete nextLineErrors[key];
        return { ...current, lineErrors: nextLineErrors };
      });
    }
  }

  function addLine() {
    setValues((v) => ({ ...v, lines: [...v.lines, emptyLine(crypto.randomUUID())] }));
  }

  function removeLine(key: string) {
    setValues((v) => (v.lines.length <= 1 ? v : { ...v, lines: v.lines.filter((l) => l.key !== key) }));
    setErrors((current) => {
      if (!current.lineErrors?.[key]) return current;
      const nextLineErrors = { ...current.lineErrors };
      delete nextLineErrors[key];
      return { ...current, lineErrors: nextLineErrors };
    });
    lineContainerRefs.current.delete(key);
  }

  function focusInvalid(found: PurchaseOrderErrors) {
    if (found.number) {
      numberContainerRef.current?.querySelector<HTMLElement>("input")?.focus();
      return;
    }
    if (found.supplier) {
      supplierContainerRef.current?.querySelector<HTMLElement>('input[role="combobox"]')?.focus();
      return;
    }
    if (found.expectedDate) {
      expectedDateContainerRef.current?.querySelector<HTMLElement>("input")?.focus();
      return;
    }
    if (found.lineErrors) {
      const [key, rowErrors] = Object.entries(found.lineErrors)[0] ?? [];
      if (!key || !rowErrors) return;
      const field: LineField = rowErrors.sku ? "sku" : rowErrors.quantity ? "quantity" : "unitPrice";
      lineContainerRefs.current.get(key)?.querySelector<HTMLElement>(`[name="${field}"]`)?.focus();
    }
  }

  async function submit() {
    if (submitting) return;
    setFormError(null);
    const found = validatePurchaseOrder(values);
    setErrors(found);
    if (hasErrors(found)) {
      focusInvalid(found);
      return;
    }

    setSubmitting(true);
    try {
      const payload = buildPurchaseOrderPayload(values);
      const created = await createPurchaseOrder(payload);
      announce(`Purchase order ${created.po_number} created.`);
      onCreated(created);
    } catch (err) {
      // The server's own message, verbatim — never rewritten, never swallowed. The form's typed
      // values are untouched: nothing above this line clears `values`.
      setFormError(
        err instanceof Error ? err.message : "Couldn't reach the server. Nothing was saved — try again.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  const supplierOption: ComboboxOption | null = values.supplierCode
    ? { id: values.supplierCode, label: values.supplierName ?? values.supplierCode, sublabel: values.supplierCode }
    : null;

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="New purchase order"
      description="Raise a purchase order against a supplier. It can still be adjusted before it's approved."
      size="lg"
      initialFocusRef={initialFocusRef}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={submitting}>
            Cancel
          </Button>
          <Button variant="primary" onClick={() => void submit()} disabled={submitting}>
            {submitting ? "Creating…" : "Create purchase order"}
          </Button>
        </>
      }
    >
      {/* One summary, announced once. A `role="alert"` per field would interrupt several times on
          a multi-error submit; every field carries its own message via `aria-describedby`. */}
      <div aria-live="polite" className="empty:hidden">
        {formError !== null ? (
          <InlineAlert tone="danger" title="That purchase order wasn't created" className="mb-4">
            {formError}
          </InlineAlert>
        ) : null}
      </div>

      <form
        className="flex flex-col gap-5"
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <div className="grid gap-4 sm:grid-cols-2">
          <div ref={numberContainerRef}>
            <TextField
              label="PO number"
              value={values.number}
              onChange={(e) => {
                setValues((v) => ({ ...v, number: e.target.value }));
                clearError("number");
              }}
              placeholder="PO-2026-0147"
              disabled={submitting}
              {...(errors.number !== undefined ? { error: errors.number } : {})}
            />
          </div>
          <div ref={supplierContainerRef}>
            <Combobox
              label="Supplier"
              value={supplierOption}
              onChange={(option) => {
                setValues((v) => ({
                  ...v,
                  supplierCode: option?.id ?? null,
                  supplierName: option?.label ?? null,
                }));
                clearError("supplier");
              }}
              loadOptions={loadSupplierOptions}
              placeholder="Search by name or code…"
              copy={SUPPLIER_COMBOBOX_COPY}
              disabled={submitting}
              {...(errors.supplier !== undefined ? { error: errors.supplier } : {})}
            />
          </div>
        </div>

        <div className="grid gap-4 sm:grid-cols-3">
          <Select
            label="Status"
            options={STATUS_OPTIONS}
            value={values.status}
            onChange={(e) => setValues((v) => ({ ...v, status: e.target.value as CreatableStatus }))}
            disabled={submitting}
          />
          <TextField
            label="Warehouse"
            value={values.warehouse}
            onChange={(e) => setValues((v) => ({ ...v, warehouse: e.target.value }))}
            placeholder="Pune DC"
            optional
            disabled={submitting}
          />
          <Select
            label="Currency"
            options={CURRENCY_OPTIONS}
            value={values.currency}
            onChange={(e) => setValues((v) => ({ ...v, currency: e.target.value }))}
            disabled={submitting}
          />
        </div>

        <div ref={expectedDateContainerRef} className="sm:max-w-64">
          <TextField
            label="Expected date"
            type="date"
            value={values.expectedDate}
            onChange={(e) => {
              setValues((v) => ({ ...v, expectedDate: e.target.value }));
              clearError("expectedDate");
            }}
            disabled={submitting}
            {...(errors.expectedDate !== undefined ? { error: errors.expectedDate } : {})}
          />
        </div>

        <TextArea
          label="Notes"
          value={values.notes}
          onChange={(e) => setValues((v) => ({ ...v, notes: e.target.value }))}
          rows={3}
          placeholder="Q4 raw materials"
          optional
          disabled={submitting}
        />

        <fieldset className="m-0 flex flex-col gap-3 border-0 p-0">
          <legend className="mb-1 px-0 text-xs font-semibold uppercase tracking-wide text-faint">
            Line items — {values.lines.length} line{values.lines.length === 1 ? "" : "s"}
          </legend>

          <div className="flex flex-col gap-3">
            {values.lines.map((line, index) => {
              const rowErrors = errors.lineErrors?.[line.key];
              const amount = lineAmountMinor(line);
              return (
                <div
                  key={line.key}
                  ref={(el) => {
                    if (el) lineContainerRefs.current.set(line.key, el);
                    else lineContainerRefs.current.delete(line.key);
                  }}
                  role="group"
                  aria-label={`Line ${index + 1}`}
                  className="flex flex-col gap-3 rounded-tile border border-line p-4"
                >
                  <div className="flex items-center justify-between gap-3">
                    <p className="text-xs font-semibold uppercase tracking-wide text-faint">
                      Line {index + 1}
                    </p>
                    <button
                      type="button"
                      onClick={() => removeLine(line.key)}
                      disabled={values.lines.length <= 1 || submitting}
                      aria-label={`Remove line ${index + 1}`}
                      className="grid size-8 shrink-0 place-items-center rounded-full text-muted transition-colors hover:bg-ground hover:text-ink disabled:cursor-not-allowed disabled:opacity-40"
                    >
                      <CloseIcon className="size-4" />
                    </button>
                  </div>
                  <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
                    <TextField
                      label="SKU"
                      name="sku"
                      value={line.sku}
                      onChange={(e) => updateLineField(line.key, "sku", e.target.value)}
                      placeholder="AL-6061-T6"
                      disabled={submitting}
                      {...(rowErrors?.sku !== undefined ? { error: rowErrors.sku } : {})}
                    />
                    <TextField
                      label="Description"
                      value={line.description}
                      onChange={(e) => updateLineField(line.key, "description", e.target.value)}
                      placeholder="Aluminium billet"
                      optional
                      disabled={submitting}
                      containerClassName="sm:col-span-2 lg:col-span-1"
                    />
                    <TextField
                      label="Quantity"
                      name="quantity"
                      inputMode="numeric"
                      value={line.quantity}
                      onChange={(e) => updateLineField(line.key, "quantity", e.target.value)}
                      placeholder="40"
                      disabled={submitting}
                      {...(rowErrors?.quantity !== undefined ? { error: rowErrors.quantity } : {})}
                    />
                    <TextField
                      label={`Unit price (${values.currency})`}
                      name="unitPrice"
                      inputMode="decimal"
                      value={line.unitPrice}
                      onChange={(e) => updateLineField(line.key, "unitPrice", e.target.value)}
                      placeholder="2250"
                      disabled={submitting}
                      {...(rowErrors?.unitPrice !== undefined ? { error: rowErrors.unitPrice } : {})}
                    />
                    <TextField
                      label="Unit"
                      value={line.uom}
                      onChange={(e) => updateLineField(line.key, "uom", e.target.value)}
                      placeholder="kg"
                      optional
                      disabled={submitting}
                    />
                  </div>
                  <p className="text-right text-sm tnum text-muted">
                    Line total{" "}
                    <span className="font-semibold text-ink">
                      {formatMoney(amount ?? 0, values.currency)}
                    </span>
                  </p>
                </div>
              );
            })}
          </div>

          <div className="flex items-center justify-between gap-3">
            <Button
              variant="outline"
              size="sm"
              onClick={addLine}
              disabled={submitting}
              leadingIcon={<PlusIcon className="size-4" />}
            >
              Add line
            </Button>
            {errors.lines !== undefined ? (
              <p className="text-xs font-medium text-negative">{errors.lines}</p>
            ) : null}
          </div>

          <div className="flex items-center justify-between rounded-tile bg-ground px-4 py-3">
            <span className="text-sm font-semibold text-ink">Order total</span>
            <span className="display text-lg font-extrabold tnum">
              {formatMoney(orderTotalMinor(values.lines), values.currency)}
            </span>
          </div>
        </fieldset>
      </form>
    </Dialog>
  );
}
