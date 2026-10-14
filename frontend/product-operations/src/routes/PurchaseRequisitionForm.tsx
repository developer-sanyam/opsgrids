import { useEffect, useMemo, useState } from "react";
import type { FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { createRequisition, getRequisition, listUoms, updateRequisition } from "@/api/client";
import type { InventoryItemOut, RequisitionOut } from "@/api/types";
import { useWarehouseIdOptions } from "@/data/useWarehouseOptions";
import { OrderItemPicker } from "@/forms/OrderItemPicker";
import { problemMessage, useAsync, useAuth, useMutation } from "@opsgrids/client";
import { minorToInput } from "@/lib/money";
import { today } from "@/lib/now";
import {
  buildRequisition,
  buildRequisitionPatch,
  emptyRequisition,
  emptyRequisitionLine,
  hasRequisitionErrors,
  validateRequisition,
} from "@/lib/requisitionForm";
import type {
  RequisitionErrors,
  RequisitionFormValues,
  RequisitionLineDraft,
  RequisitionLineErrors,
} from "@/lib/requisitionForm";
import {
  Button,
  Card,
  FormSection,
  InlineAlert,
  LoadError,
  PageHeader,
  Select,
  Skeleton,
  TextArea,
  TextField,
  UnsavedChangesGuard,
} from "@/ui";
import type { ComboboxOption } from "@/ui";
import { ArrowRightIcon, FieldGrid, MinusCircleIcon, PlusIcon, useToast } from "@opsgrids/ui";

let rowSeq = 0;
function nextRowKey(): string {
  rowSeq += 1;
  return `req-row-${rowSeq}`;
}

function valuesFrom(req: RequisitionOut): RequisitionFormValues {
  return {
    warehouseId: req.warehouse_id ?? "",
    neededBy: req.needed_by ?? "",
    reason: req.reason ?? "",
    lines: req.lines.map((l) => ({
      key: nextRowKey(),
      itemId: l.inventory_item_id,
      sku: l.sku,
      name: l.item_name,
      uom: l.uom,
      quantity: l.quantity,
      estPrice: l.est_unit_price_minor === null ? "" : minorToInput(l.est_unit_price_minor),
      note: l.note ?? "",
    })),
  };
}

function BackLink({ to, label }: { to: string; label: string }) {
  return (
    <Link
      to={to}
      className="relative hit-44 inline-flex w-fit items-center gap-2 text-sm font-semibold text-muted hover:text-ink transition-colors"
    >
      <ArrowRightIcon className="size-4 rotate-180" />
      {label}
    </Link>
  );
}

/** New (`/purchase-requisitions/new`) and edit (`/purchase-requisitions/:id/edit`, drafts only). */
export function PurchaseRequisitionFormPage() {
  const { id } = useParams();
  const editing = id !== undefined;
  const state = useAsync(() => (id ? getRequisition(id) : Promise.resolve(null)), [id]);

  if (editing && state.status === "loading") {
    return (
      <div className="rise flex flex-col gap-6" role="status" aria-label="Loading requisition">
        <Skeleton className="h-8 w-40 rounded-tile" />
        <Card className="p-6">
          <Skeleton className="h-64 w-full rounded-tile" />
        </Card>
      </div>
    );
  }
  if (editing && state.status === "error") {
    return (
      <div className="rise flex flex-col gap-6">
        <BackLink to="/purchase-requisitions" label="Back to requisitions" />
        <Card className="p-6">
          <LoadError error={state.error} noun="this requisition" onRetry={state.reload} />
        </Card>
      </div>
    );
  }
  const req = state.status === "success" ? state.data : null;
  if (editing && req && req.status !== "DRAFT") {
    return (
      <div className="rise flex flex-col gap-6">
        <BackLink to={`/purchase-requisitions/${req.id}`} label="Back to requisition" />
        <Card className="p-6">
          <p className="text-base font-bold text-ink">This requisition can no longer be edited</p>
          <p className="mt-1 text-sm text-muted">
            {req.number} is {req.status.toLowerCase()}. Only a draft can be changed — cancel it and raise a new one
            if it needs to change.
          </p>
        </Card>
      </div>
    );
  }
  return <RequisitionFormBody requisition={req} />;
}

function RequisitionFormBody({ requisition }: { requisition: RequisitionOut | null }) {
  const navigate = useNavigate();
  const toast = useToast();
  const { user } = useAuth();
  const warehouses = useWarehouseIdOptions();
  const uoms = useAsync(() => listUoms({ size: 200 }), []);
  const discreteUoms = useMemo(
    () => new Set(uoms.status === "success" ? uoms.data.items.filter((u) => u.is_discrete).map((u) => u.code) : []),
    [uoms],
  );

  const initial = useState<RequisitionFormValues>(() =>
    requisition ? valuesFrom(requisition) : emptyRequisition(nextRowKey()),
  )[0];
  const [values, setValues] = useState<RequisitionFormValues>(initial);
  const [itemOptions, setItemOptions] = useState<Record<string, ComboboxOption | null>>(() =>
    Object.fromEntries(
      initial.lines.map((l) => [l.key, l.itemId ? { id: l.itemId, label: l.sku, sublabel: l.name } : null]),
    ),
  );
  const [errors, setErrors] = useState<RequisitionErrors>({});
  // Where to go once saved. Navigating from an effect, after `saved` has switched the unsaved-changes
  // guard off, is what keeps the guard from asking about changes that were just saved.
  const [savedTo, setSavedTo] = useState<string | null>(null);
  const dirty = JSON.stringify(values) !== JSON.stringify(initial);
  useEffect(() => {
    if (savedTo !== null) navigate(savedTo);
  }, [savedTo, navigate]);

  const save = useMutation(
    (v: RequisitionFormValues) =>
      requisition
        ? updateRequisition(requisition.id, buildRequisitionPatch(v))
        : createRequisition(buildRequisition(v, user?.full_name ?? "")),
    {
      onSuccess: (saved) => {
        toast.success(requisition ? `${saved.number} saved.` : `${saved.number} created as a draft.`);
        setSavedTo(`/purchase-requisitions/${saved.id}`);
      },
    },
  );

  function updateLine(key: string, next: Partial<RequisitionLineDraft>) {
    setValues((v) => ({ ...v, lines: v.lines.map((l) => (l.key === key ? { ...l, ...next } : l)) }));
  }

  function pickItem(key: string, option: ComboboxOption | null, item: InventoryItemOut | null) {
    setItemOptions((prev) => ({ ...prev, [key]: option }));
    updateLine(
      key,
      option === null
        ? { itemId: null, sku: "", name: "", uom: "" }
        : { itemId: option.id, sku: option.label, name: option.sublabel ?? "", uom: item?.uom ?? "" },
    );
  }

  function removeLine(key: string) {
    setValues((v) => ({ ...v, lines: v.lines.filter((l) => l.key !== key) }));
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const found = validateRequisition(values, { today: today(), discreteUoms });
    setErrors(found);
    if (hasRequisitionErrors(found)) return;
    void save.mutate(values);
  }

  const backTo = requisition ? `/purchase-requisitions/${requisition.id}` : "/purchase-requisitions";
  const serverLine = (index: number, field: string): string | undefined => save.fieldErrors[`lines.${index}.${field}`];
  const hasFieldErrors = Object.keys(save.fieldErrors).length > 0;

  return (
    <div className="rise flex flex-col gap-6">
      <BackLink to={backTo} label={requisition ? "Back to requisition" : "Back to requisitions"} />
      <PageHeader
        eyebrow="Procurement"
        title={requisition ? `Edit ${requisition.number}` : "New purchase requisition"}
        actions={
          <Button type="button" variant="ghost" disabled={save.pending} onClick={() => navigate(backTo)}>
            Cancel
          </Button>
        }
      />
      <UnsavedChangesGuard when={dirty && savedTo === null} subject={requisition ? requisition.number : "this requisition"} />

      <form noValidate onSubmit={submit} className="flex flex-col gap-6">
        <Card className="p-4 sm:p-6">
          <FormSection title="Request details" description="Where the stock is needed, and by when." headingLevel={2}>
            <Select
              label="Warehouse"
              value={values.warehouseId}
              onChange={(e) => setValues((v) => ({ ...v, warehouseId: e.target.value }))}
              placeholder={warehouses.status === "loading" ? "Loading warehouses…" : "No warehouse chosen"}
              options={warehouses.options}
              disabled={warehouses.status !== "success"}
              {...(save.fieldErrors.warehouse_id !== undefined ? { error: save.fieldErrors.warehouse_id } : {})}
            />
            <TextField
              label="Needed by"
              type="date"
              value={values.neededBy}
              min={requisition ? undefined : today()}
              onChange={(e) => setValues((v) => ({ ...v, neededBy: e.target.value }))}
              {...((errors.neededBy ?? save.fieldErrors.needed_by) !== undefined
                ? { error: errors.neededBy ?? save.fieldErrors.needed_by }
                : {})}
            />
            <TextArea
              label="Reason"
              value={values.reason}
              onChange={(e) => setValues((v) => ({ ...v, reason: e.target.value }))}
              optional
              maxLength={2000}
              counterFrom={1800}
              containerClassName="col-span-full"
              {...(save.fieldErrors.reason !== undefined ? { error: save.fieldErrors.reason } : {})}
            />
          </FormSection>
        </Card>

        <Card className="p-4 sm:p-6 flex flex-col gap-4">
          <FormSection title="Lines" description="What is needed, in each item's own unit." columns={1} headingLevel={2}>
            <div className="col-span-full flex items-center justify-between gap-3">
              <span className="text-sm font-bold text-ink">
                {values.lines.length} line{values.lines.length === 1 ? "" : "s"}
              </span>
              <Button
                type="button"
                variant="outline"
                size="sm"
                leadingIcon={<PlusIcon className="size-4" />}
                disabled={values.lines.length >= 200}
                onClick={() => setValues((v) => ({ ...v, lines: [...v.lines, emptyRequisitionLine(nextRowKey())] }))}
              >
                Add line
              </Button>
            </div>

            <div aria-live="polite" className="col-span-full empty:hidden">
              {errors.lines ? <InlineAlert tone="danger" title={errors.lines} /> : null}
            </div>

            {values.lines.map((line, index) => {
              const lineErrors: RequisitionLineErrors = errors.lineErrors?.[line.key] ?? {};
              const quantityError = lineErrors.quantity ?? serverLine(index, "quantity");
              const itemError = lineErrors.item ?? serverLine(index, "inventory_item_id");
              const priceError = lineErrors.estPrice ?? serverLine(index, "est_unit_price_minor");
              return (
                <div key={line.key} className="flex flex-col gap-3 rounded-tile border border-line p-4">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-xs font-semibold uppercase tracking-wide text-faint">Line {index + 1}</span>
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      onClick={() => removeLine(line.key)}
                      disabled={values.lines.length <= 1}
                      leadingIcon={<MinusCircleIcon className="size-4" />}
                    >
                      Remove
                    </Button>
                  </div>
                  <OrderItemPicker
                    value={itemOptions[line.key] ?? null}
                    onChange={(option) => {
                      if (option === null) pickItem(line.key, null, null);
                    }}
                    onPick={(item) => {
                      if (item) pickItem(line.key, { id: item.id, label: item.sku, sublabel: item.name }, item);
                    }}
                    {...(itemError !== undefined ? { error: itemError } : {})}
                  />
                  <FieldGrid className="sm:grid-cols-3">
                    <TextField
                      label="Quantity"
                      inputMode="decimal"
                      value={line.quantity}
                      onChange={(e) => updateLine(line.key, { quantity: e.target.value })}
                      required
                      helper={line.uom ? `In ${line.uom}.` : "Choose an item to see its unit."}
                      {...(quantityError !== undefined ? { error: quantityError } : {})}
                    />
                    <TextField
                      label="Estimated unit price"
                      inputMode="decimal"
                      value={line.estPrice}
                      onChange={(e) => updateLine(line.key, { estPrice: e.target.value })}
                      optional
                      helper="Major units, like 24000.50."
                      {...(priceError !== undefined ? { error: priceError } : {})}
                    />
                    <TextField
                      label="Note"
                      value={line.note}
                      onChange={(e) => updateLine(line.key, { note: e.target.value })}
                      optional
                      maxLength={300}
                      {...(serverLine(index, "note") !== undefined ? { error: serverLine(index, "note") } : {})}
                    />
                  </FieldGrid>
                </div>
              );
            })}
          </FormSection>

          {save.error && !hasFieldErrors ? <InlineAlert tone="danger" title={problemMessage(save.error)} /> : null}

          <div className="flex justify-end border-t border-line pt-4">
            <Button type="submit" loading={save.pending}>
              {requisition ? "Save changes" : "Create requisition"}
            </Button>
          </div>
        </Card>
      </form>
    </div>
  );
}
