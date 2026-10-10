import { useEffect, useRef, useState } from "react";

import { createAdjustment } from "@/api/movements";
import type { AdjustmentIn, AdjustmentOut, AdjustmentReason } from "@/api/movementTypes";
import type { InventoryItemOut } from "@/api/types";
import { useMutation } from "@opsgrids/client";
import { ADJUSTMENT_REASONS, validateAdjustmentForm } from "@/lib/adjustmentForm";
import type { AdjustmentFormErrors } from "@/lib/adjustmentForm";
import { formDialogError } from "@/lib/formError";
import { formatQty } from "@/lib/quantity";
import { today } from "@/lib/now";
import { stockRefusals } from "@/lib/stockRefusal";
import { useSubmissionKeys } from "@/lib/useSubmissionKeys";
import { useWarehouseIdOptions } from "@/data/useWarehouseOptions";
import { errorProp } from "@/movements/errorProp";
import { MovementItemPicker } from "@/movements/MovementItemPicker";
import { StockRefusalAlert } from "@/movements/StockRefusalAlert";
import { TrackingFields } from "@/movements/TrackingFields";
import { useDiscreteUoms } from "@/movements/useMovementReference";
import { WarehouseBinFields } from "@/movements/WarehouseBinFields";
import { AdjustmentStatusPill, Button, FormDialog, InlineAlert, Select, TextArea, TextField } from "@/ui";
import type { ComboboxOption } from "@/ui";
import { Dialog } from "@opsgrids/ui";

interface AdjustmentRequestDialogProps {
  onClose: () => void;
  /** The person has read the outcome and closed the result. */
  onDone: (result: AdjustmentOut) => void;
}

/** What the server did with the request, in one sentence the person can act on. */
export function AdjustmentResult({ result }: { result: AdjustmentOut }) {
  const change = `${result.quantity_delta.startsWith("-") ? "" : "+"}${formatQty(result.quantity_delta)} ${result.uom}`;
  if (result.status === "PENDING_APPROVAL") {
    const approver = result.approver_name ?? "the approval service";
    return (
      <InlineAlert tone="warning" title={`${result.number} is waiting for ${approver}`}>
        {result.sku}: {change} at {result.location_code}. The stock has not changed yet — it changes only if the
        adjustment is approved. Until then you can withdraw it from the adjustments list.
      </InlineAlert>
    );
  }
  if (result.status === "APPLIED") {
    return (
      <InlineAlert tone="success" title={`${result.number} was applied`}>
        {result.sku}: {change} at {result.location_code}. The stock is already updated.
      </InlineAlert>
    );
  }
  return (
    <InlineAlert tone="info" title={`${result.number} is ${result.status.toLowerCase().replace("_", " ")}`}>
      {result.sku}: {change} at {result.location_code}.
    </InlineAlert>
  );
}

/**
 * `POST /operations/inventory/adjustments` — `operations.inventory.stock.adjust`. A signed change
 * to one bin with a reason. Under the organization's threshold it applies at once (HTTP 201);
 * at or over it, it is held for approval (202) and must carry an evidence document — when the
 * server asks for one (422 on `evidence_document_id`) that field becomes required and takes focus.
 * The dialog ends on a result that says which of the two happened.
 */
export function AdjustmentRequestDialog({ onClose, onDone }: AdjustmentRequestDialogProps) {
  const [itemOption, setItemOption] = useState<ComboboxOption | null>(null);
  const [item, setItem] = useState<InventoryItemOut | null>(null);
  const [warehouseId, setWarehouseId] = useState("");
  const [locationId, setLocationId] = useState("");
  const [delta, setDelta] = useState("");
  const [reason, setReason] = useState<AdjustmentReason | "">("");
  const [note, setNote] = useState("");
  const [evidence, setEvidence] = useState("");
  const [occurredOn, setOccurredOn] = useState(today());
  const [lotId, setLotId] = useState("");
  const [rollId, setRollId] = useState("");
  const [errors, setErrors] = useState<AdjustmentFormErrors>({});
  const [result, setResult] = useState<AdjustmentOut | null>(null);
  const deltaRef = useRef<HTMLInputElement>(null);
  const evidenceRef = useRef<HTMLInputElement>(null);

  const warehouses = useWarehouseIdOptions();
  const discrete = useDiscreteUoms();
  const keyFor = useSubmissionKeys();

  const save = useMutation(
    (input: AdjustmentIn) => createAdjustment(input, keyFor(input)),
    { onSuccess: (saved) => setResult(saved) },
  );
  const refusals = stockRefusals(save.error);
  const field = save.fieldErrors;
  const evidenceRequested = field.evidence_document_id !== undefined;

  useEffect(() => {
    if (evidenceRequested) evidenceRef.current?.focus();
  }, [evidenceRequested]);

  function onPick(picked: InventoryItemOut | null) {
    setItem(picked);
    setLotId("");
    setRollId("");
    if (picked === null) return;
    const home = warehouses.options.find((w) => w.label === picked.warehouse);
    if (home) {
      setWarehouseId(home.value);
      setLocationId("");
    }
  }

  function submit() {
    const outcome = validateAdjustmentForm(
      {
        inventoryItemId: itemOption?.id ?? "",
        uom: item?.uom ?? "",
        locationId,
        delta,
        reason,
        note,
        evidenceDocumentId: evidence,
        occurredOn,
        lotId,
        rollId,
      },
      discrete,
      today(),
    );
    if (!outcome.ok) {
      setErrors(outcome.errors);
      return;
    }
    setErrors({});
    void save.mutate(outcome.payload);
  }

  if (result !== null) {
    return (
      <Dialog
        open
        onClose={() => onDone(result)}
        title={result.status === "PENDING_APPROVAL" ? "Submitted for approval" : "Adjustment recorded"}
        footer={<Button onClick={() => onDone(result)}>Done</Button>}
      >
        <div className="flex flex-col gap-4" aria-live="polite">
          <div className="flex items-center gap-2 text-sm text-muted">
            Status <AdjustmentStatusPill status={result.status} />
          </div>
          <AdjustmentResult result={result} />
        </div>
      </Dialog>
    );
  }

  const dirty = itemOption !== null || delta !== "" || reason !== "" || note !== "" || evidence !== "";

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title="Request a stock adjustment"
      description="Change the quantity in one bin, with the reason. A large adjustment is held for approval and needs evidence."
      submitLabel="Submit adjustment"
      pendingLabel="Submitting…"
      pending={save.pending}
      initialFocusRef={deltaRef}
      dirty={dirty}
      {...(refusals.length > 0 ? {} : formDialogError(save.error, save.fieldErrors))}
    >
      <MovementItemPicker
        value={itemOption}
        onChange={setItemOption}
        onPick={onPick}
        {...errorProp(errors.item, field.inventory_item_id)}
      />
      <WarehouseBinFields
        warehouseOptions={warehouses.options}
        warehousesLoading={warehouses.status === "loading"}
        warehouseId={warehouseId}
        onWarehouseChange={setWarehouseId}
        locationId={locationId}
        onLocationChange={setLocationId}
        locationLabel="Bin to adjust"
        {...(errors.location !== undefined || field.location_id !== undefined
          ? { locationError: errors.location ?? field.location_id }
          : {})}
      />
      <TextField
        ref={deltaRef}
        label="Quantity change"
        inputMode="decimal"
        value={delta}
        onChange={(e) => setDelta(e.target.value)}
        helper={
          item
            ? `Positive adds, negative removes ${item.uom}. On hand: ${item.on_hand_qty} ${item.uom}.`
            : "Positive adds, negative removes. Up to 3 decimals."
        }
        required
        {...errorProp(errors.delta, field.quantity_delta)}
      />
      <TrackingFields
        tracksLot={item?.track_lot ?? false}
        tracksRoll={item?.track_roll ?? false}
        lotId={lotId}
        rollId={rollId}
        onLotChange={setLotId}
        onRollChange={setRollId}
        lotError={errors.lot ?? field.lot_id}
        rollError={errors.roll ?? field.roll_id}
      />
      <Select
        label="Reason"
        value={reason}
        onChange={(e) => setReason(e.target.value as AdjustmentReason)}
        placeholder="Choose a reason"
        options={ADJUSTMENT_REASONS}
        required
        {...errorProp(errors.reason, field.reason)}
      />
      <TextField
        label="Date"
        type="date"
        value={occurredOn}
        max={today()}
        onChange={(e) => setOccurredOn(e.target.value)}
        {...errorProp(errors.occurredOn, field.occurred_on)}
      />
      <TextField
        ref={evidenceRef}
        label="Evidence document id"
        value={evidence}
        onChange={(e) => setEvidence(e.target.value)}
        required={evidenceRequested}
        optional={!evidenceRequested}
        helper="The id of a supporting document — a photo or a count sheet. Required when this adjustment is large enough to need approval; the document library doesn't have a picker for this yet, so paste its id."
        {...errorProp(errors.evidence, field.evidence_document_id)}
      />
      <TextArea
        label="Note"
        value={note}
        onChange={(e) => setNote(e.target.value)}
        optional
        maxLength={500}
        counterFrom={400}
        {...errorProp(field.note)}
      />
      <StockRefusalAlert refusals={refusals} action="take this out" />
    </FormDialog>
  );
}
