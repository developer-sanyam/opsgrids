import { useRef, useState } from "react";

import { convertRequisition } from "@/api/client";
import type { PurchaseOrderOut, RequisitionConvertIn, RequisitionOut, SupplierOut } from "@/api/types";
import { useWarehouseOptions } from "@/data/useWarehouseOptions";
import { SupplierPicker } from "@/forms/SupplierPicker";
import { useMutation } from "@opsgrids/client";
import { formDialogError } from "@/lib/formError";
import { minorToInput, parseMinor } from "@/lib/money";
import { today } from "@/lib/now";
import { FormDialog, FormSection, Select, TextArea, TextField } from "@/ui";
import type { ComboboxOption } from "@/ui";

interface ConvertRequisitionDialogProps {
  requisition: RequisitionOut;
  onClose: () => void;
  onConverted: (po: PurchaseOrderOut) => void;
}

/**
 * `POST /operations/purchase-requisitions/{id}/convert` — turns an approved requisition into one
 * draft purchase order with the chosen supplier. Each line carries the requisition's own estimate
 * as its starting price and can be overridden here; a line left blank sends no override. A line
 * with a fractional quantity cannot become a purchase order line, and the server's 422 names it.
 */
export function ConvertRequisitionDialog({ requisition, onClose, onConverted }: ConvertRequisitionDialogProps) {
  const warehouses = useWarehouseOptions();
  const [supplier, setSupplier] = useState<ComboboxOption | null>(null);
  const [supplierRecord, setSupplierRecord] = useState<SupplierOut | null>(null);
  const [expectedDate, setExpectedDate] = useState("");
  const [warehouse, setWarehouse] = useState(requisition.warehouse_name ?? "");
  const [currency, setCurrency] = useState("");
  const [notes, setNotes] = useState("");
  const [prices, setPrices] = useState<Record<string, string>>(() =>
    Object.fromEntries(
      requisition.lines.map((l) => [l.id, l.est_unit_price_minor === null ? "" : minorToInput(l.est_unit_price_minor)]),
    ),
  );

  const [supplierError, setSupplierError] = useState<string | undefined>(undefined);
  const [dateError, setDateError] = useState<string | undefined>(undefined);
  const [priceErrors, setPriceErrors] = useState<Record<string, string>>({});
  const firstFieldRef = useRef<HTMLInputElement>(null);

  const save = useMutation((input: RequisitionConvertIn) => convertRequisition(requisition.id, input), {
    onSuccess: (po) => onConverted(po),
  });

  const dirty = supplier !== null || expectedDate !== "" || currency !== "" || notes !== "";

  function submit() {
    setSupplierError(undefined);
    setDateError(undefined);
    setPriceErrors({});

    if (supplier === null) {
      setSupplierError("Choose the supplier this order goes to.");
      return;
    }
    if (expectedDate === "") {
      setDateError("Pick an expected delivery date.");
      firstFieldRef.current?.focus();
      return;
    }
    if (expectedDate < today()) {
      setDateError("The expected date can't be in the past.");
      firstFieldRef.current?.focus();
      return;
    }

    const found: Record<string, string> = {};
    const overrides: { line_id: string; unit_price_minor: number }[] = [];
    for (const line of requisition.lines) {
      const typed = prices[line.id] ?? "";
      if (typed.trim() === "") continue;
      const parsed = parseMinor(typed);
      if (parsed.ok) overrides.push({ line_id: line.id, unit_price_minor: parsed.minor });
      else found[line.id] = parsed.message;
    }
    if (Object.keys(found).length > 0) {
      setPriceErrors(found);
      return;
    }

    void save.mutate({
      supplier_id: supplier.id,
      expected_date: expectedDate,
      ...(warehouse !== "" ? { warehouse } : {}),
      ...(currency.trim() !== "" ? { currency: currency.trim().toUpperCase() } : {}),
      ...(notes.trim() !== "" ? { notes: notes.trim() } : {}),
      ...(overrides.length > 0 ? { prices: overrides } : {}),
    });
  }

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title={`Convert ${requisition.number} to a purchase order`}
      description="Creates one draft purchase order with this supplier. You can still edit it before it is approved."
      submitLabel="Create purchase order"
      pendingLabel="Creating…"
      pending={save.pending}
      initialFocusRef={firstFieldRef}
      size="lg"
      dirty={dirty}
      {...formDialogError(save.error, save.fieldErrors)}
    >
      <FormSection title="Order" headingLevel={3}>
        <div className="col-span-full">
          <SupplierPicker
            value={supplier}
            onChange={(option) => {
              setSupplier(option);
              if (option === null) setSupplierRecord(null);
            }}
            onPick={(record) => {
              setSupplierRecord(record);
              if (record && currency === "") setCurrency(record.currency);
            }}
            {...((supplierError ?? save.fieldErrors.supplier_id) !== undefined
              ? { error: supplierError ?? save.fieldErrors.supplier_id }
              : {})}
          />
        </div>
        <TextField
          ref={firstFieldRef}
          label="Expected date"
          type="date"
          value={expectedDate}
          min={today()}
          onChange={(e) => setExpectedDate(e.target.value)}
          required
          {...((dateError ?? save.fieldErrors.expected_date) !== undefined
            ? { error: dateError ?? save.fieldErrors.expected_date }
            : {})}
        />
        <Select
          label="Warehouse"
          value={warehouse}
          onChange={(e) => setWarehouse(e.target.value)}
          placeholder={warehouses.status === "loading" ? "Loading warehouses…" : "Use the requisition's warehouse"}
          options={warehouses.options}
          disabled={warehouses.status !== "success"}
          {...(save.fieldErrors.warehouse !== undefined ? { error: save.fieldErrors.warehouse } : {})}
        />
        <TextField
          label="Currency"
          value={currency}
          onChange={(e) => setCurrency(e.target.value.toUpperCase())}
          maxLength={3}
          optional
          helper={
            supplierRecord ? `${supplierRecord.name} bills in ${supplierRecord.currency}.` : "Leave blank to use the supplier's."
          }
          {...(save.fieldErrors.currency !== undefined ? { error: save.fieldErrors.currency } : {})}
        />
        <TextArea
          label="Notes"
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          optional
          maxLength={2000}
          containerClassName="col-span-full"
          {...(save.fieldErrors.notes !== undefined ? { error: save.fieldErrors.notes } : {})}
        />
      </FormSection>

      <FormSection
        title="Prices"
        description="Each line starts at the requisition's estimate. Change it here for this order."
        columns={1}
        headingLevel={3}
      >
        {requisition.lines.map((line, index) => (
          <TextField
            key={line.id}
            label={`Unit price: ${line.sku} (${line.quantity} ${line.uom})`}
            inputMode="decimal"
            value={prices[line.id] ?? ""}
            onChange={(e) => setPrices((p) => ({ ...p, [line.id]: e.target.value }))}
            optional
            {...((priceErrors[line.id] ?? save.fieldErrors[`prices.${index}.unit_price_minor`]) !== undefined
              ? { error: priceErrors[line.id] ?? save.fieldErrors[`prices.${index}.unit_price_minor`] }
              : {})}
          />
        ))}
      </FormSection>
    </FormDialog>
  );
}
