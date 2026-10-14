import { useState } from "react";
import type { FormEvent } from "react";
import { useNavigate } from "react-router-dom";

import { createOrder, quoteOrder } from "@/api/client";
import type { InventoryItemOut } from "@/api/types";
import { useWarehouseIdOptions } from "@/data/useWarehouseOptions";
import { problemMessage, useAsync, useMutation } from "@opsgrids/client";
import {
  buildOrderPayload,
  emptyOrderForm,
  emptyOrderLine,
  hasOrderFormErrors,
  quotableOrderLines,
  validateOrderForm,
} from "@/lib/orderForm";
import type {
  OrderEntryMode,
  OrderFormErrors,
  OrderFormValues,
  OrderLineDraft,
  OrderLineErrors,
} from "@/lib/orderForm";
import { OrderItemPicker } from "@/forms/OrderItemPicker";
import { QuotePreview } from "@/forms/QuotePreview";
import {
  Button,
  Card,
  FormSection,
  InlineAlert,
  PageHeader,
  RadioGroup,
  Select,
  Stepper,
  StepperActions,
  TextField,
  UnsavedChangesGuard,
} from "@/ui";
import type { ComboboxOption, RadioOption, StepItem } from "@/ui";
import { MinusCircleIcon, PlusIcon, useDebouncedValue, useToast } from "@opsgrids/ui";

const STEPS: StepItem[] = [
  { id: "customer", title: "Customer & warehouse", description: "Who this order is for, and where it ships from" },
  { id: "lines", title: "Line items", description: "What's on the order, priced live" },
];

const MODE_OPTIONS: RadioOption[] = [
  { value: "phone", label: "Phone", description: "A caller phoned this order in." },
  { value: "manual", label: "Manual", description: "Taken at the counter — typed directly, no lookup." },
  { value: "sales", label: "Sales", description: "An operator-authored order." },
];

let rowSeq = 0;
function nextRowKey(): string {
  rowSeq += 1;
  return `row-${rowSeq}`;
}

/** One order line — an item picker, a quantity, and an optional price override. Stacks as a card
 *  at every width, the same shape the purchase order's line editor uses. */
function OrderLineEditor({
  line,
  index,
  itemOption,
  errors,
  serverErrors,
  onChange,
  onPickItem,
  onRemove,
  canRemove,
}: {
  line: OrderLineDraft;
  index: number;
  itemOption: ComboboxOption | null;
  errors: OrderLineErrors;
  serverErrors: Record<string, string>;
  onChange: (next: OrderLineDraft) => void;
  onPickItem: (option: ComboboxOption | null, item: InventoryItemOut | null) => void;
  onRemove: () => void;
  canRemove: boolean;
}) {
  const itemError = errors.item ?? serverErrors[`lines.${index}.inventory_item_id`];
  const quantityError = errors.quantity ?? serverErrors[`lines.${index}.quantity`];
  const priceError = errors.price ?? serverErrors[`lines.${index}.selling_price_minor`];

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

      <OrderItemPicker
        value={itemOption}
        onChange={(option) => onPickItem(option, null)}
        onPick={(item) =>
          onPickItem(item ? { id: item.id, label: item.sku, sublabel: item.name } : itemOption, item)
        }
        {...(itemError !== undefined ? { error: itemError } : {})}
      />
      {line.itemName ? <p className="text-xs text-muted">{line.itemName}</p> : null}

      <div className="grid grid-cols-1 gap-3 min-[400px]:grid-cols-2">
        <TextField
          label="Quantity"
          inputMode="decimal"
          value={line.quantity}
          onChange={(e) => onChange({ ...line, quantity: e.target.value })}
          required
          {...(quantityError !== undefined ? { error: quantityError } : {})}
        />
        <TextField
          label="Price override"
          inputMode="decimal"
          value={line.priceOverride}
          onChange={(e) => onChange({ ...line, priceOverride: e.target.value })}
          optional
          helper="Leave blank to use the item's own selling price."
          {...(priceError !== undefined ? { error: priceError } : {})}
        />
      </div>
    </div>
  );
}

export function OrderFormPage() {
  const navigate = useNavigate();
  const toast = useToast();
  const warehouses = useWarehouseIdOptions();

  const initialValues = useState<OrderFormValues>(() => emptyOrderForm("phone", nextRowKey()))[0];
  const [values, setValues] = useState<OrderFormValues>(initialValues);
  // The item combobox's selection is UI-only — the wire value is `OrderLineDraft.inventoryItemId`.
  const [itemOptions, setItemOptions] = useState<Record<string, ComboboxOption | null>>({});
  const [errors, setErrors] = useState<OrderFormErrors>({});
  const [step, setStep] = useState(0);

  const create = useMutation((payload: ReturnType<typeof buildOrderPayload>) => createOrder(payload), {
    onSuccess: (saved) => {
      toast.success(`${saved.number} created as a draft.`);
      navigate(`/orders/${saved.id}`);
    },
  });

  const dirty = JSON.stringify(values) !== JSON.stringify(initialValues);

  function updateLine(index: number, next: OrderLineDraft) {
    setValues((v) => ({ ...v, lines: v.lines.map((line, i) => (i === index ? next : line)) }));
  }

  function pickItem(key: string, index: number, option: ComboboxOption | null, item: InventoryItemOut | null) {
    setItemOptions((prev) => ({ ...prev, [key]: option }));
    setValues((v) => ({
      ...v,
      lines: v.lines.map((line, i) =>
        i === index
          ? {
              ...line,
              inventoryItemId: item ? item.id : "",
              sku: item ? item.sku : "",
              itemName: item ? item.name : "",
            }
          : line,
      ),
    }));
  }

  function removeLine(index: number) {
    const key = values.lines[index]?.key;
    setValues((v) => ({ ...v, lines: v.lines.filter((_, i) => i !== index) }));
    if (key) {
      setItemOptions((prev) => {
        const next = { ...prev };
        delete next[key];
        return next;
      });
    }
  }

  const detailsInvalid = Boolean(errors.warehouse ?? errors.customer ?? create.fieldErrors.warehouse_id);
  const linesInvalid = Boolean(errors.lines ?? errors.lineErrors);
  const stepItems: StepItem[] = STEPS.map((item, index) => ({
    ...item,
    invalid: index === 0 ? detailsInvalid : linesInvalid,
  }));

  function continueToLines() {
    const found = validateOrderForm(values);
    setErrors(found);
    if (found.warehouse ?? found.customer) return;
    setStep(1);
  }

  function submit() {
    const found = validateOrderForm(values);
    setErrors(found);
    if (hasOrderFormErrors(found)) {
      if (found.warehouse ?? found.customer) setStep(0);
      return;
    }
    void create.mutate(buildOrderPayload(values));
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (step === 0) continueToLines();
    else submit();
  }

  // A live pricing preview as the operator edits lines — debounced so every keystroke doesn't
  // fire its own request. Keyed on a plain string so `useDebouncedValue` settles on content, not
  // on a fresh object identity created every render (unrelated field edits included).
  const quotable = quotableOrderLines(values);
  const quoteReady = values.warehouseId !== "" && quotable.length > 0;
  const quoteKey = useDebouncedValue(JSON.stringify({ warehouseId: values.warehouseId, lines: quotable }), 400);
  const quoteLoader = () => {
    const parsed = JSON.parse(quoteKey) as { warehouseId: string; lines: typeof quotable };
    if (parsed.warehouseId === "" || parsed.lines.length === 0) return Promise.resolve(null);
    return quoteOrder({ warehouse_id: parsed.warehouseId, lines: parsed.lines });
  };
  const quoteState = useAsync(quoteLoader, [quoteKey]);

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Fulfilment"
        title="New order"
        actions={
          <Button type="button" variant="ghost" onClick={() => navigate("/orders")} disabled={create.pending}>
            Cancel
          </Button>
        }
      />
      <UnsavedChangesGuard when={dirty} subject="this order" />

      <form noValidate onSubmit={handleSubmit} className="grid grid-cols-1 gap-4 md:grid-cols-4">
        <Card className="p-3 md:col-span-1 md:self-start">
          <Stepper steps={stepItems} current={step} onStepChange={setStep} />
        </Card>

        <div className="flex flex-col gap-6 md:col-span-3">
          {step === 0 ? (
            <Card className="p-4 sm:p-6">
              <FormSection
                title="Customer & warehouse"
                description="How this order is coming in, who it's for, and which warehouse fills it."
                headingLevel={3}
              >
                <div className="col-span-full">
                  <RadioGroup
                    legend="How is this order coming in?"
                    name="order-entry-mode"
                    value={values.mode}
                    onChange={(v) => setValues((val) => ({ ...val, mode: v as OrderEntryMode }))}
                    options={MODE_OPTIONS}
                  />
                </div>

                {values.mode === "phone" ? (
                  <div className="col-span-full">
                    <InlineAlert tone="info" title="Looking a caller up isn't available yet">
                      Enter the caller's name and phone number below — they'll still be saved on the
                      order the same way a matched customer would be.
                    </InlineAlert>
                  </div>
                ) : null}

                <div aria-live="polite" className="col-span-full empty:hidden">
                  {errors.customer ? <InlineAlert tone="danger" title={errors.customer} /> : null}
                </div>

                <TextField
                  label="Customer name"
                  value={values.customerName}
                  onChange={(e) => setValues((v) => ({ ...v, customerName: e.target.value }))}
                  optional
                  {...(create.fieldErrors.customer_name !== undefined
                    ? { error: create.fieldErrors.customer_name }
                    : {})}
                />
                <TextField
                  label="Customer phone"
                  type="tel"
                  value={values.customerPhone}
                  onChange={(e) => setValues((v) => ({ ...v, customerPhone: e.target.value }))}
                  optional
                  {...(create.fieldErrors.customer_phone !== undefined
                    ? { error: create.fieldErrors.customer_phone }
                    : {})}
                />
                <Select
                  label="Warehouse"
                  value={values.warehouseId}
                  onChange={(e) => setValues((v) => ({ ...v, warehouseId: e.target.value }))}
                  placeholder="Choose a warehouse"
                  options={warehouses.options}
                  required
                  {...((errors.warehouse ?? create.fieldErrors.warehouse_id) !== undefined
                    ? { error: errors.warehouse ?? create.fieldErrors.warehouse_id }
                    : {})}
                />
              </FormSection>
              <StepperActions continueLabel="Continue" className="mt-6" />
            </Card>
          ) : null}

          {step === 1 ? (
            <Card className="p-4 sm:p-6 flex flex-col gap-4">
              <FormSection title="Line items" description="What's on the order." columns={1} headingLevel={3}>
                <div className="col-span-full flex items-center justify-between gap-3">
                  <span className="text-sm font-bold text-ink">
                    {values.lines.length} line{values.lines.length === 1 ? "" : "s"}
                  </span>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    leadingIcon={<PlusIcon className="size-4" />}
                    onClick={() => setValues((v) => ({ ...v, lines: [...v.lines, emptyOrderLine(nextRowKey())] }))}
                    disabled={values.lines.length >= 200}
                  >
                    Add line
                  </Button>
                </div>

                <div aria-live="polite" className="col-span-full empty:hidden">
                  {errors.lines ? <InlineAlert tone="danger" title={errors.lines} /> : null}
                </div>

                {values.lines.map((line, index) => (
                  <OrderLineEditor
                    key={line.key}
                    line={line}
                    index={index}
                    itemOption={itemOptions[line.key] ?? null}
                    errors={errors.lineErrors?.[line.key] ?? {}}
                    serverErrors={create.fieldErrors}
                    onChange={(next) => updateLine(index, next)}
                    onPickItem={(option, item) => pickItem(line.key, index, option, item)}
                    onRemove={() => removeLine(index)}
                    canRemove={values.lines.length > 1}
                  />
                ))}
              </FormSection>

              <div className="flex flex-col gap-2 border-t border-line pt-4">
                <span className="text-sm font-bold text-ink">Pricing &amp; availability</span>
                <QuotePreview
                  status={!quoteReady ? "idle" : quoteState.status}
                  quote={quoteState.status === "success" ? quoteState.data : null}
                  {...(quoteState.status === "error"
                    ? { error: quoteState.error, onRetry: quoteState.reload }
                    : {})}
                  idleMessage={
                    values.warehouseId === ""
                      ? "Choose a warehouse to see pricing."
                      : "Pick an item and a quantity to see pricing and availability."
                  }
                />
              </div>

              {create.error && Object.keys(create.fieldErrors).length === 0 ? (
                <InlineAlert tone="danger" title={problemMessage(create.error)} />
              ) : null}

              <StepperActions
                onBack={() => setStep(0)}
                final
                pending={create.pending}
                pendingLabel="Creating…"
                continueLabel="Create order"
              />
            </Card>
          ) : null}
        </div>
      </form>
    </div>
  );
}
