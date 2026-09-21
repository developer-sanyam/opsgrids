import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { createPurchaseOrder, getPurchaseOrder, updatePurchaseOrder } from "@/api/client";
import type { PurchaseOrderIn, PurchaseOrderOut, PurchaseOrderPatch } from "@/api/types";
import { useWarehouseOptions } from "@/data/useWarehouseOptions";
import { useAuth, problemMessage, useAsync, useMutation } from "@opsgrids/client";
import { minorToInput } from "@/lib/money";
import { formatMoney } from "@/lib/format";
import { today } from "@/lib/now";
import {
  buildPurchaseOrderPayload,
  emptyLine,
  emptyPurchaseOrder,
  hasErrors,
  lineAmountMinor,
  orderTotalMinor,
  validatePurchaseOrder,
} from "@/lib/purchaseOrderForm";
import type { LineDraft, LineErrors, PurchaseOrderErrors, PurchaseOrderFormValues } from "@/lib/purchaseOrderForm";
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

function linesFromPo(po: PurchaseOrderOut): LineDraft[] {
  if (po.line_items.length === 0) return [emptyLine(nextRowKey())];
  return po.line_items.map((line) => ({
    key: nextRowKey(),
    sku: line.sku,
    description: line.description,
    quantity: String(line.quantity),
    unitPrice: minorToInput(line.unit_price_minor),
    uom: line.uom,
  }));
}

/** One line of the purchase order — stacks as a card at every width, so it stays usable at 360px. */
function LineEditor({
  line,
  index,
  skuOption,
  errors,
  serverErrors,
  onChange,
  onPickSku,
  onRemove,
  canRemove,
  currency,
}: {
  line: LineDraft;
  index: number;
  skuOption: ComboboxOption | null;
  errors: LineErrors;
  serverErrors: Record<string, string>;
  onChange: (next: LineDraft) => void;
  onPickSku: (option: ComboboxOption | null, prefill: Partial<LineDraft>) => void;
  onRemove: () => void;
  canRemove: boolean;
  currency: string;
}) {
  const amount = lineAmountMinor(line);

  const skuServerError = serverErrors[`lines.${index}.sku`];
  const descriptionServerError = serverErrors[`lines.${index}.description`];
  const quantityError = errors.quantity ?? serverErrors[`lines.${index}.quantity`];
  const uomServerError = serverErrors[`lines.${index}.uom`];
  const unitPriceError = errors.unitPrice ?? serverErrors[`lines.${index}.unit_price_minor`];
  const skuError = errors.sku ?? skuServerError;

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
        value={skuOption}
        onChange={(option) => onPickSku(option, option ? { sku: option.id } : {})}
        onPick={(item) =>
          onPickSku(
            item ? { id: item.sku, label: item.sku, sublabel: item.name } : skuOption,
            item
              ? {
                  sku: item.sku,
                  description: item.name,
                  uom: item.uom,
                  unitPrice: minorToInput(item.unit_value_minor),
                }
              : {},
          )
        }
        {...(skuError !== undefined ? { error: skuError } : {})}
      />

      <TextField
        label="SKU (free text for a non-stock line)"
        value={line.sku}
        onChange={(e) => onChange({ ...line, sku: e.target.value })}
        helper="Prefilled when you pick a stocked item above; type your own for services or freight."
      />

      <TextField
        label="Description"
        value={line.description}
        onChange={(e) => onChange({ ...line, description: e.target.value })}
        optional
        {...(descriptionServerError !== undefined ? { error: descriptionServerError } : {})}
      />

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <TextField
          label="Quantity"
          inputMode="numeric"
          value={line.quantity}
          onChange={(e) => onChange({ ...line, quantity: e.target.value })}
          {...(quantityError !== undefined ? { error: quantityError } : {})}
        />
        <TextField
          label="UoM"
          value={line.uom}
          onChange={(e) => onChange({ ...line, uom: e.target.value })}
          optional
          {...(uomServerError !== undefined ? { error: uomServerError } : {})}
        />
        <TextField
          label="Unit price"
          inputMode="decimal"
          value={line.unitPrice}
          onChange={(e) => onChange({ ...line, unitPrice: e.target.value })}
          containerClassName="col-span-2 sm:col-span-1"
          {...(unitPriceError !== undefined ? { error: unitPriceError } : {})}
        />
        <div className="col-span-2 sm:col-span-1 flex flex-col justify-end pb-2.5">
          <span className="text-xs font-semibold uppercase tracking-wide text-faint">Line total</span>
          <span className="tnum font-bold text-ink">{formatMoney(amount ?? 0, currency)}</span>
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
  const [values, setValues] = useState<PurchaseOrderFormValues>(() =>
    po
      ? {
          supplierId: po.supplier_id,
          supplierName: po.supplier_name,
          warehouse: po.warehouse,
          currency: "",
          expectedDate: po.expected_date,
          notes: po.notes ?? "",
          lines: linesFromPo(po),
        }
      : emptyPurchaseOrder(nextRowKey()),
  );
  // The SKU combobox's selection is UI-only — the wire value is `LineDraft.sku`, a plain string,
  // so a free-text SKU with no inventory match still round-trips without a matching option.
  const [skuOptions, setSkuOptions] = useState<Record<string, ComboboxOption | null>>({});
  const [supplierOption, setSupplierOption] = useState<ComboboxOption | null>(
    po ? { id: po.supplier_id, label: po.supplier_name } : null,
  );

  const [errors, setErrors] = useState<PurchaseOrderErrors>({});

  const save = useMutation(
    (payload: PurchaseOrderIn | PurchaseOrderPatch) =>
      po ? updatePurchaseOrder(po.id, payload) : createPurchaseOrder(payload as PurchaseOrderIn),
    { onSuccess: (saved) => onSaved(saved) },
  );

  const total = orderTotalMinor(values.lines);
  const currency = po ? po.currency : values.currency || "INR";

  function updateLine(index: number, next: LineDraft) {
    setValues((v) => ({ ...v, lines: v.lines.map((line, i) => (i === index ? next : line)) }));
  }

  function pickSku(key: string, index: number, option: ComboboxOption | null, prefill: Partial<LineDraft>) {
    setSkuOptions((prev) => ({ ...prev, [key]: option }));
    if (Object.keys(prefill).length > 0) {
      setValues((v) => ({
        ...v,
        lines: v.lines.map((line, i) => (i === index ? { ...line, ...prefill } : line)),
      }));
    }
  }

  function removeLine(index: number) {
    const key = values.lines[index]?.key;
    setValues((v) => ({ ...v, lines: v.lines.filter((_, i) => i !== index) }));
    if (key) setSkuOptions((prev) => { const next = { ...prev }; delete next[key]; return next; });
  }

  function submit() {
    const mode = po ? ("edit" as const) : ("create" as const);
    const found = validatePurchaseOrder(values, { mode, today: today() });
    setErrors(found);
    if (hasErrors(found)) return;

    const payload = po
      ? buildPurchaseOrderPayload(values, { mode: "edit" })
      : buildPurchaseOrderPayload(values, { mode: "create", raisedBy: raisedByName });
    void save.mutate(payload);
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
            value={supplierOption}
            onChange={(option) => {
              setSupplierOption(option);
              setValues((v) => ({ ...v, supplierId: option?.id ?? null, supplierName: option?.label ?? null }));
            }}
            onPick={(record) => {
              if (record && values.currency === "") setValues((v) => ({ ...v, currency: record.currency }));
            }}
            {...(errors.supplier !== undefined ? { error: errors.supplier } : {})}
          />
        )}

        <div className="grid gap-4 sm:grid-cols-2">
          <Select
            label="Warehouse"
            value={values.warehouse}
            onChange={(e) => setValues((v) => ({ ...v, warehouse: e.target.value }))}
            placeholder="Choose a warehouse"
            options={warehouseOptions}
            {...((errors.warehouse ?? save.fieldErrors.warehouse) !== undefined
              ? { error: errors.warehouse ?? save.fieldErrors.warehouse }
              : {})}
          />
          <TextField
            label="Expected date"
            type="date"
            value={values.expectedDate}
            {...(po ? {} : { min: today() })}
            onChange={(e) => setValues((v) => ({ ...v, expectedDate: e.target.value }))}
            {...((errors.expectedDate ?? save.fieldErrors.expected_date) !== undefined
              ? { error: errors.expectedDate ?? save.fieldErrors.expected_date }
              : {})}
          />
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <TextField
            label="Currency"
            value={values.currency}
            onChange={(e) => setValues((v) => ({ ...v, currency: e.target.value.toUpperCase() }))}
            maxLength={3}
            optional
            helper={
              po
                ? "Leave blank to keep the order's current currency."
                : "Leave blank to use the supplier's currency."
            }
            {...(save.fieldErrors.currency !== undefined ? { error: save.fieldErrors.currency } : {})}
          />
        </div>

        <TextArea
          label="Notes"
          value={values.notes}
          onChange={(e) => setValues((v) => ({ ...v, notes: e.target.value }))}
          optional
          maxLength={2000}
          counterFrom={1800}
          {...(save.fieldErrors.notes !== undefined ? { error: save.fieldErrors.notes } : {})}
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
            onClick={() => setValues((v) => ({ ...v, lines: [...v.lines, emptyLine(nextRowKey())] }))}
            disabled={values.lines.length >= 200}
          >
            Add line
          </Button>
        </div>

        <div aria-live="polite" className="empty:hidden">
          {errors.lines ? <InlineAlert tone="danger" title={errors.lines} /> : null}
        </div>

        <div className="flex flex-col gap-3">
          {values.lines.map((line, index) => (
            <LineEditor
              key={line.key}
              line={line}
              index={index}
              skuOption={skuOptions[line.key] ?? null}
              errors={errors.lineErrors?.[line.key] ?? {}}
              serverErrors={save.fieldErrors}
              currency={currency}
              onChange={(next) => updateLine(index, next)}
              onPickSku={(option, prefill) => pickSku(line.key, index, option, prefill)}
              onRemove={() => removeLine(index)}
              canRemove={values.lines.length > 1}
            />
          ))}
        </div>

        <div className="flex items-center justify-between border-t border-line pt-4">
          <span className="text-sm font-bold text-ink">Order total</span>
          <span className="display text-xl font-extrabold tnum">{formatMoney(total, currency)}</span>
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
