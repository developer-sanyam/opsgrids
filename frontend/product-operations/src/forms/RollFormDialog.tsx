import { useRef, useState } from "react";

import { createRoll } from "@/api/tracking";
import type { RollIn, RollOut } from "@/api/trackingTypes";
import type { InventoryItemOut } from "@/api/types";
import { checkQuantity, checkWholeNumber } from "@/lib/qtyInput";
import { ItemPicker } from "@/tracking/ItemPicker";
import { useLotOptions } from "@/tracking/useTrackedOptions";
import { useStockOperation } from "@/tracking/useStockOperation";
import { FormDialog, InlineAlert, Select, TextField } from "@/ui";

const ROLL_NO_PATTERN = /^[A-Za-z0-9._/-]+$/;

interface RollFormDialogProps {
  /** The item the roll is of. `null` asks for one, among roll-tracked items. */
  item: InventoryItemOut | null;
  onClose: () => void;
  onSaved: (roll: RollOut) => void;
}

/**
 * Register a roll — `POST /operations/inventory/rolls` (`operations.inventory.roll.write`). A roll
 * holds no stock until opening stock is posted into it. The length is the primary quantity, in the
 * item's own unit and validated against it; the measured weight is a separate fact with its own
 * unit, never derived from the length, and the two are sent together or not at all.
 */
export function RollFormDialog({ item: initialItem, onClose, onSaved }: RollFormDialogProps) {
  const [item, setItem] = useState<InventoryItemOut | null>(initialItem);
  const [lotId, setLotId] = useState("");
  const [rollNo, setRollNo] = useState("");
  const [primary, setPrimary] = useState("");
  const [secondary, setSecondary] = useState("");
  const [secondaryUom, setSecondaryUom] = useState("");
  const [widthMm, setWidthMm] = useState("");
  const [gsm, setGsm] = useState("");
  const [shade, setShade] = useState("");
  const [grade, setGrade] = useState("");

  const [errors, setErrors] = useState<Record<string, string>>({});
  const firstFieldRef = useRef<HTMLInputElement>(null);

  const lots = useLotOptions(item?.track_lot ? item.id : "");
  const op = useStockOperation(createRoll, onSaved, [
    ...(initialItem === null ? ["inventory_item_id"] : []),
    ...(item?.track_lot ? ["lot_id"] : []),
    "roll_no",
    "primary_qty",
    "secondary_qty",
    "secondary_uom",
    "width_mm",
    "gsm",
    "shade",
    "grade",
  ]);

  const dirty =
    (initialItem === null && item !== null) ||
    [lotId, rollNo, primary, secondary, secondaryUom, widthMm, gsm, shade, grade].some((v) => v !== "");

  function submit() {
    const found: Record<string, string> = {};
    if (item === null) found["inventory_item_id"] = "Choose the item this roll is of.";
    else if (!item.track_roll) {
      found["inventory_item_id"] = `${item.sku} isn't tracked by roll. Turn on roll tracking for the item first.`;
    }
    if (item?.track_lot && lotId === "") found["lot_id"] = "Choose the lot this roll belongs to.";
    const trimmedRollNo = rollNo.trim();
    if (trimmedRollNo !== "" && (trimmedRollNo.length > 60 || !ROLL_NO_PATTERN.test(trimmedRollNo))) {
      found["roll_no"] = "Letters, numbers, dots, slashes, hyphens and underscores only, up to 60.";
    }
    const length = checkQuantity(primary, { uom: item?.uom ?? "" });
    if (!length.ok) found["primary_qty"] = length.message;

    const weightGiven = secondary.trim() !== "";
    const unitGiven = secondaryUom.trim() !== "";
    const weight = weightGiven ? checkQuantity(secondary, { uom: secondaryUom }) : null;
    if (weight && !weight.ok) found["secondary_qty"] = weight.message;
    if (weightGiven && !unitGiven) found["secondary_uom"] = "Say what the weight is measured in, like kg.";
    if (unitGiven && !weightGiven) found["secondary_qty"] = "Enter the measured quantity, or clear the unit.";

    const width = widthMm.trim() === "" ? null : checkWholeNumber(widthMm, "Width");
    if (width && !width.ok) found["width_mm"] = width.message;
    const gsmValue = gsm.trim() === "" ? null : checkWholeNumber(gsm, "GSM");
    if (gsmValue && !gsmValue.ok) found["gsm"] = gsmValue.message;

    setErrors(found);
    if (Object.keys(found).length > 0 || item === null || !length.ok) {
      if (found["roll_no"]) firstFieldRef.current?.focus();
      return;
    }
    const input: RollIn = {
      inventory_item_id: item.id,
      primary_qty: length.value,
      ...(lotId ? { lot_id: lotId } : {}),
      ...(trimmedRollNo ? { roll_no: trimmedRollNo } : {}),
      ...(weight?.ok ? { secondary_qty: weight.value, secondary_uom: secondaryUom.trim() } : {}),
      ...(width?.ok ? { width_mm: width.value } : {}),
      ...(gsmValue?.ok ? { gsm: gsmValue.value } : {}),
      ...(shade.trim() ? { shade: shade.trim() } : {}),
      ...(grade.trim() ? { grade: grade.trim() } : {}),
    };
    op.submit(input);
  }

  const err = (field: string): string | undefined => errors[field] ?? op.fieldErrors[field];

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title="Create roll"
      description={initialItem ? `${initialItem.sku} · ${initialItem.name}` : undefined}
      submitLabel="Create roll"
      pendingLabel="Creating…"
      pending={op.pending}
      initialFocusRef={firstFieldRef}
      size="lg"
      dirty={dirty}
      {...op.dialogError}
    >
      {initialItem !== null && !initialItem.track_roll ? (
        <InlineAlert tone="warning" title={`${initialItem.sku} isn't tracked by roll`}>
          Turn on roll tracking for the item first. A roll can only be registered for a roll-tracked item.
        </InlineAlert>
      ) : null}
      {initialItem === null ? (
        <ItemPicker
          value={item}
          onChange={(next) => {
            setItem(next);
            setLotId("");
          }}
          requires="track_roll"
          helper="Only items with roll tracking turned on."
          error={err("inventory_item_id")}
        />
      ) : null}
      {item?.track_lot ? (
        <Select
          label="Lot"
          value={lotId}
          onChange={(e) => setLotId(e.target.value)}
          placeholder={lots.status === "loading" ? "Loading lots…" : "Choose a lot"}
          options={lots.options}
          disabled={lots.status !== "success" || lots.options.length === 0}
          required
          helper={
            lots.status === "success" && lots.options.length === 0
              ? "This item has no lots yet. Create a lot first."
              : undefined
          }
          error={err("lot_id")}
        />
      ) : null}
      <TextField
        ref={firstFieldRef}
        label="Roll number"
        value={rollNo}
        onChange={(e) => setRollNo(e.target.value)}
        optional
        helper="Leave blank to generate one."
        error={err("roll_no")}
      />
      <TextField
        label={item ? `Length (${item.uom})` : "Length"}
        inputMode="decimal"
        value={primary}
        onChange={(e) => setPrimary(e.target.value)}
        required
        helper="In the item's own unit, up to 3 decimals."
        error={err("primary_qty")}
      />
      <div className="grid gap-4 sm:grid-cols-2">
        <TextField
          label="Measured weight"
          inputMode="decimal"
          value={secondary}
          onChange={(e) => setSecondary(e.target.value)}
          optional
          helper="A separate measurement, not worked out from the length."
          error={err("secondary_qty")}
        />
        <TextField
          label="Weight unit"
          value={secondaryUom}
          onChange={(e) => setSecondaryUom(e.target.value)}
          optional
          placeholder="kg"
          error={err("secondary_uom")}
        />
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <TextField
          label="Width (mm)"
          inputMode="numeric"
          value={widthMm}
          onChange={(e) => setWidthMm(e.target.value)}
          optional
          error={err("width_mm")}
        />
        <TextField
          label="GSM"
          inputMode="numeric"
          value={gsm}
          onChange={(e) => setGsm(e.target.value)}
          optional
          error={err("gsm")}
        />
        <TextField
          label="Shade"
          value={shade}
          onChange={(e) => setShade(e.target.value)}
          optional
          maxLength={40}
          error={err("shade")}
        />
        <TextField
          label="Grade"
          value={grade}
          onChange={(e) => setGrade(e.target.value)}
          optional
          maxLength={20}
          error={err("grade")}
        />
      </div>
    </FormDialog>
  );
}
