import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { createPurchaseOrder, getPurchaseOrder, updatePurchaseOrder } from "@/api/client";
import type { InventoryItemOut, PoLineIn, PurchaseOrderIn, PurchaseOrderOut } from "@/api/types";
import { useWarehouseOptions } from "@/data/useWarehouseOptions";
import { useAuth, problemMessage, useAsync, useMutation } from "@opsgrids/client";
import { minorToInput, parseMinor } from "@/lib/money";
import { formatMoney } from "@/lib/format";
import { today } from "@/lib/now";
import { Button, Card, ErrorState, InlineAlert, PageHeader, Select, Skeleton, TextArea, TextField } from "@/ui";
import type { ComboboxOption } from "@/ui";
import { ArrowRightIcon, MinusCircleIcon, PlusIcon, useToast } from "@opsgrids/ui";
import { SupplierPicker } from "@/forms/SupplierPicker";
import { SkuPicker } from "@/forms/SkuPicker";

let rowSeq = 0;
function nextRowKey(): string {
  rowSeq += 1;
  return `row-${rowSeq}`;
}

interface LineRow {
  key: string;
  sku: ComboboxOption | null;
  skuText: string;
  description: string;
  uom: string;
  quantity: string;
  unitPrice: string;
}

function emptyRow(): LineRow {
  return { key: nextRowKey(), sku: null, skuText: "", description: "", uom: "", quantity: "1", unitPrice: "" };
}

function rowFromLine(line: PurchaseOrderOut["line_items"][number]): LineRow {
  return {
    key: nextRowKey(),
    sku: null,
    skuText: line.sku,
    description: line.description,
    uom: line.uom,
    quantity: String(line.quantity),
    unitPrice: minorToInput(line.unit_price_minor),
  };
}

interface RowErrors {
  sku?: string;
  quantity?: string;
  unitPrice?: string;
}

/** One line of the purchase order — stacks as a card at every width, so it stays usable at 360px. */
function LineEditor({
  row,
  index,
  errors,
  serverErrors,
  onChange,
  onRemove,
  canRemove,
}: {
  row: LineRow;
  index: number;
  errors: RowErrors;
  serverErrors: Record<string, string>;
  onChange: (next: LineRow) => void;
  onRemove: () => void;
  canRemove: boolean;
}) {
  const lineTotal = (() => {
    const qty = Number.parseInt(row.quantity, 10);
    const price = parseMinor(row.unitPrice);
    if (!Number.isFinite(qty) || !price.ok) return 0;
    return qty * price.minor;
  })();

  const skuServerError = serverErrors[`lines.${index}.sku`];
  const descriptionServerError = serverErrors[`lines.${index}.description`];
  const quantityError = errors.quantity ?? serverErrors[`lines.${index}.quantity`];
  const uomServerError = serverErrors[`lines.${index}.uom`];
  const unitPriceError = errors.unitPrice ?? serverErrors[`lines.${index}.unit_price_minor`];

  return (
    <div className="flex flex-col gap-3 rounded-tile border border-line p-4">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-semibold uppercase tracking-wide text-faint">Line {index + 1}</span>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={onRemove}
          disabled={!canRemove}
          leadingIcon={<MinusCircleIcon className="size-4" />}
        >
          Remove
        </Button>
      </div>

      <SkuPicker
        value={row.sku}
        onChange={(option) =>
          onChange({ ...row, sku: option, skuText: option ? option.id : row.skuText })
        }
        onPick={(item: InventoryItemOut | null) =>
          onChange({
            ...row,
            sku: item ? { id: item.sku, label: item.sku, sublabel: item.name } : row.sku,
            skuText: item ? item.sku : row.skuText,
            description: item ? item.name : row.description,
            uom: item ? item.uom : row.uom,
            unitPrice: item ? minorToInput(item.unit_value_minor) : row.unitPrice,
          })
        }
        {...((errors.sku) !== undefined ? { error: errors.sku } : {})}
      />

      <TextField
        label="SKU (free text for a non-stock line)"
        value={row.skuText}
        onChange={(e) => onChange({ ...row, skuText: e.target.value, sku: null })}
        helper="Prefilled when you pick a stocked item above; type your own for services or freight."
        {...(skuServerError !== undefined ? { error: skuServerError } : {})}
      />

      <TextField
        label="Description"
        value={row.description}
        onChange={(e) => onChange({ ...row, description: e.target.value })}
        optional
        {...(descriptionServerError !== undefined ? { error: descriptionServerError } : {})}
      />

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <TextField
          label="Quantity"
          inputMode="numeric"
          value={row.quantity}
          onChange={(e) => onChange({ ...row, quantity: e.target.value })}
          {...(quantityError !== undefined ? { error: quantityError } : {})}
        />
        <TextField
          label="UoM"
          value={row.uom}
          onChange={(e) => onChange({ ...row, uom: e.target.value })}
          optional
          {...(uomServerError !== undefined ? { error: uomServerError } : {})}
        />
        <TextField
          label="Unit price"
          inputMode="decimal"
          value={row.unitPrice}
          onChange={(e) => onChange({ ...row, unitPrice: e.target.value })}
          containerClassName="col-span-2 sm:col-span-1"
          {...(unitPriceError !== undefined ? { error: unitPriceError } : {})}
        />
        <div className="col-span-2 sm:col-span-1 flex flex-col justify-end pb-2.5">
          <span className="text-xs font-semibold uppercase tracking-wide text-faint">Line total</span>
          <span className="tnum font-bold text-ink">{formatMoney(lineTotal)}</span>
        </div>
      </div>
    </div>
  );
}

export function PurchaseOrderFormPage() {
  const { id } = useParams();
  const editing = id !== undefined;
  const navigate = useNavigate();
  const toast = useToast();
  const { user } = useAuth();
  const warehouses = useWarehouseOptions();

  const poLoader = () => (id ? getPurchaseOrder(id) : Promise.resolve(null));
  const poState = useAsync(poLoader, [id]);

  if (editing && poState.status === "loading") {
    return (
      <div className="rise flex flex-col gap-6">
        <Skeleton className="h-8 w-40 rounded-tile" />
        <Card className="p-6"><Skeleton className="h-64 w-full rounded-tile" /></Card>
      </div>
    );
  }

  if (editing && poState.status === "error") {
    return (
      <div className="rise flex flex-col gap-6">
        <BackLink id={id} />
        <Card className="p-6">
          <ErrorState message={problemMessage(poState.error)} onRetry={poState.reload} />
        </Card>
      </div>
    );
  }

  const po = editing && poState.status === "success" ? poState.data : null;

  if (editing && po && po.status !== "draft") {
    return (
      <div className="rise flex flex-col gap-6">
        <BackLink id={id} />
        <Card className="p-6">
          <ErrorState
            title="This purchase order can no longer be edited"
            message={`${po.po_number} is ${po.status}. Only a draft can be changed — cancel it instead if it needs to stop.`}
            tone="neutral"
          />
        </Card>
      </div>
    );
  }

  return (
    <PurchaseOrderFormBody
      po={po}
      warehouseOptions={warehouses.options}
      raisedByName={user?.full_name ?? ""}
      onCancel={() => navigate(editing && id ? `/purchase-orders/${id}` : "/purchase-orders")}
      onSaved={(saved) => {
        toast.success(editing ? `${saved.po_number} saved.` : `${saved.po_number} created.`);
        navigate(`/purchase-orders/${saved.id}`);
      }}
    />
  );
}

function BackLink({ id }: { id?: string }) {
  return (
    <Link
      to={id ? `/purchase-orders/${id}` : "/purchase-orders"}
      className="inline-flex w-fit items-center gap-1.5 text-sm font-semibold text-muted hover:text-ink transition-colors"
    >
      <ArrowRightIcon className="size-4 rotate-180" />
      {id ? "Back to purchase order" : "Back to purchase orders"}
    </Link>
  );
}

function PurchaseOrderFormBody({
  po,
  warehouseOptions,
  raisedByName,
  onCancel,
  onSaved,
}: {
  po: PurchaseOrderOut | null;
  warehouseOptions: { value: string; label: string }[];
  raisedByName: string;
  onCancel: () => void;
  onSaved: (po: PurchaseOrderOut) => void;
}) {
  const [supplier, setSupplier] = useState<ComboboxOption | null>(
    po ? { id: po.supplier_id, label: po.supplier_name } : null,
  );
  const [warehouse, setWarehouse] = useState(po?.warehouse ?? "");
  const [expectedDate, setExpectedDate] = useState(po?.expected_date ?? "");
  const [currency, setCurrency] = useState(po?.currency ?? "");
  const [notes, setNotes] = useState(po?.notes ?? "");
  const [lines, setLines] = useState<LineRow[]>(
    po && po.line_items.length > 0 ? po.line_items.map(rowFromLine) : [emptyRow()],
  );

  const [supplierError, setSupplierError] = useState<string | undefined>(undefined);
  const [warehouseError, setWarehouseError] = useState<string | undefined>(undefined);
  const [dateError, setDateError] = useState<string | undefined>(undefined);
  const [linesError, setLinesError] = useState<string | undefined>(undefined);
  const [rowErrors, setRowErrors] = useState<Record<number, RowErrors>>({});

  const save = useMutation(
    (input: PurchaseOrderIn) =>
      po ? updatePurchaseOrder(po.id, input) : createPurchaseOrder(input),
    { onSuccess: (saved) => onSaved(saved) },
  );

  const total = lines.reduce((sum, row) => {
    const qty = Number.parseInt(row.quantity, 10);
    const price = parseMinor(row.unitPrice);
    if (!Number.isFinite(qty) || !price.ok) return sum;
    return sum + qty * price.minor;
  }, 0);

  function updateRow(index: number, next: LineRow) {
    setLines((prev) => prev.map((row, i) => (i === index ? next : row)));
  }

  function removeRow(index: number) {
    setLines((prev) => prev.filter((_, i) => i !== index));
  }

  function submit() {
    setSupplierError(undefined);
    setWarehouseError(undefined);
    setDateError(undefined);
    setLinesError(undefined);
    setRowErrors({});

    if (!po && supplier === null) {
      setSupplierError("Choose a supplier.");
      return;
    }
    if (warehouse === "") {
      setWarehouseError("Choose a warehouse.");
      return;
    }
    if (expectedDate === "") {
      setDateError("Choose an expected date.");
      return;
    }
    if (!po && expectedDate < today()) {
      setDateError("The expected date can't be in the past.");
      return;
    }
    if (lines.length === 0) {
      setLinesError("Add at least one line.");
      return;
    }

    const nextRowErrors: Record<number, RowErrors> = {};
    let firstBad = -1;
    const parsedLines: PoLineIn[] = [];
    lines.forEach((row, index) => {
      const errors: RowErrors = {};
      const sku = row.skuText.trim();
      if (sku === "") {
        errors.sku = "Enter a SKU.";
      }
      const qty = Number.parseInt(row.quantity, 10);
      if (!Number.isFinite(qty) || qty < 1 || String(qty) !== row.quantity.trim()) {
        errors.quantity = "Enter a whole number of at least 1.";
      }
      const price = parseMinor(row.unitPrice);
      if (!price.ok) {
        errors.unitPrice = price.message;
      }
      if (Object.keys(errors).length > 0) {
        nextRowErrors[index] = errors;
        if (firstBad === -1) firstBad = index;
      } else {
        parsedLines.push({
          sku,
          description: row.description.trim(),
          quantity: qty,
          uom: row.uom.trim(),
          unit_price_minor: price.ok ? price.minor : 0,
        });
      }
    });

    if (firstBad !== -1) {
      setRowErrors(nextRowErrors);
      return;
    }

    if (po) {
      void save.mutate({
        supplier_id: po.supplier_id,
        warehouse,
        expected_date: expectedDate,
        currency: currency.trim().toUpperCase() || po.currency,
        notes: notes.trim(),
        lines: parsedLines,
      });
      return;
    }

    if (!supplier) return;
    void save.mutate({
      supplier_id: supplier.id,
      warehouse,
      expected_date: expectedDate,
      ...(currency.trim() ? { currency: currency.trim().toUpperCase() } : {}),
      notes: notes.trim(),
      raised_by: raisedByName,
      lines: parsedLines,
    });
  }

  return (
    <div className="rise flex flex-col gap-6">
      <BackLink {...(po ? { id: po.id } : {})} />
      <PageHeader
        eyebrow="Procurement"
        title={po ? `Edit ${po.po_number}` : "New purchase order"}
      />

      <Card className="p-4 sm:p-6 flex flex-col gap-5">
        {po ? (
          <TextField label="Supplier" value={po.supplier_name} disabled helper="A draft's supplier can't be changed here — cancel and re-raise instead." />
        ) : (
          <SupplierPicker
            value={supplier}
            onChange={setSupplier}
            onPick={(record) => {
              if (record && currency === "") setCurrency(record.currency);
            }}
            {...((supplierError) !== undefined ? { error: supplierError } : {})}
          />
        )}

        <div className="grid gap-4 sm:grid-cols-2">
          <Select
            label="Warehouse"
            value={warehouse}
            onChange={(e) => setWarehouse(e.target.value)}
            placeholder="Choose a warehouse"
            options={warehouseOptions}
            {...((warehouseError ?? save.fieldErrors.warehouse) !== undefined ? { error: warehouseError ?? save.fieldErrors.warehouse } : {})}
          />
          <TextField
            label="Expected date"
            type="date"
            value={expectedDate}
            {...(po ? {} : { min: today() })}
            onChange={(e) => setExpectedDate(e.target.value)}
            {...((dateError ?? save.fieldErrors.expected_date) !== undefined ? { error: dateError ?? save.fieldErrors.expected_date } : {})}
          />
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <TextField
            label="Currency"
            value={currency}
            onChange={(e) => setCurrency(e.target.value.toUpperCase())}
            maxLength={3}
            optional
            helper={
              po
                ? "Leave blank to keep the order's current currency."
                : "Leave blank to use the supplier's currency."
            }
            {...((save.fieldErrors.currency) !== undefined ? { error: save.fieldErrors.currency } : {})}
          />
        </div>

        <TextArea
          label="Notes"
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          optional
          maxLength={2000}
          counterFrom={1800}
          {...((save.fieldErrors.notes) !== undefined ? { error: save.fieldErrors.notes } : {})}
        />
      </Card>

      <Card className="p-4 sm:p-6 flex flex-col gap-4">
        <div className="flex items-center justify-between gap-3">
          <h2 className="text-lg font-bold tracking-tight">Line items</h2>
          <Button
            type="button"
            variant="outline"
            size="sm"
            leadingIcon={<PlusIcon className="size-4" />}
            onClick={() => setLines((prev) => [...prev, emptyRow()])}
            disabled={lines.length >= 200}
          >
            Add line
          </Button>
        </div>

        {linesError ? <InlineAlert tone="danger" title={linesError} /> : null}

        <div className="flex flex-col gap-3">
          {lines.map((row, index) => (
            <LineEditor
              key={row.key}
              row={row}
              index={index}
              errors={rowErrors[index] ?? {}}
              serverErrors={save.fieldErrors}
              onChange={(next) => updateRow(index, next)}
              onRemove={() => removeRow(index)}
              canRemove={lines.length > 1}
            />
          ))}
        </div>

        <div className="flex items-center justify-between border-t border-line pt-4">
          <span className="text-sm font-bold text-ink">Order total</span>
          <span className="display text-xl font-extrabold tnum">{formatMoney(total)}</span>
        </div>
      </Card>

      {save.error && Object.keys(save.fieldErrors).length === 0 ? (
        <InlineAlert tone="danger" title={problemMessage(save.error)} />
      ) : null}

      <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
        <Button type="button" variant="ghost" onClick={onCancel} disabled={save.pending}>
          Cancel
        </Button>
        <Button type="button" variant="primary" loading={save.pending} onClick={submit}>
          {save.pending ? (po ? "Saving…" : "Creating…") : po ? "Save changes" : "Create purchase order"}
        </Button>
      </div>
    </div>
  );
}
