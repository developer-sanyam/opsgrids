import type { TransferLineErrors, TransferLineValues } from "@/lib/transferForm";
import type { InventoryItemOut } from "@/api/types";
import { errorProp } from "./errorProp";
import { MovementItemPicker } from "./MovementItemPicker";
import { TrackingFields } from "./TrackingFields";
import { Button, Select, TextField } from "@/ui";
import type { ComboboxOption, SelectOption } from "@/ui";
import { MinusCircleIcon } from "@opsgrids/ui";

/** One transfer line as the form holds it: the typed values plus what the picker knew about the item. */
export interface TransferLineState {
  values: TransferLineValues;
  itemOption: ComboboxOption | null;
  tracksLot: boolean;
  tracksRoll: boolean;
}

interface TransferLineEditorProps {
  index: number;
  line: TransferLineState;
  binOptions: SelectOption[];
  binsLoading: boolean;
  sourceChosen: boolean;
  canRemove: boolean;
  /** Our own checks, then the server's, for each control. */
  errors: TransferLineErrors;
  serverErrors: Record<string, string>;
  /** An updater, not a value: picking an item fires two callbacks in one event. */
  onChange: (update: (previous: TransferLineState) => TransferLineState) => void;
  onRemove: () => void;
}

/**
 * A line of a transfer: the item, the bin it is taken from, and the quantity. Lot and roll ids are
 * asked for only when the item is tracked by lot or roll (or the line already carries one).
 */
export function TransferLineEditor({
  index,
  line,
  binOptions,
  binsLoading,
  sourceChosen,
  canRemove,
  errors,
  serverErrors,
  onChange,
  onRemove,
}: TransferLineEditorProps) {
  const n = index + 1;
  const { values } = line;

  function setValues(patch: Partial<TransferLineValues>) {
    onChange((previous) => ({ ...previous, values: { ...previous.values, ...patch } }));
  }

  function pick(item: InventoryItemOut | null) {
    onChange((previous) => ({
      ...previous,
      values: { ...previous.values, uom: item?.uom ?? "", lotId: "", rollId: "" },
      tracksLot: item?.track_lot ?? false,
      tracksRoll: item?.track_roll ?? false,
    }));
  }

  return (
    <fieldset className="flex flex-col gap-3 rounded-tile border border-line p-4">
      <legend className="sr-only">Line {n}</legend>
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-semibold uppercase tracking-wide text-faint" aria-hidden="true">
          Line {n}
        </span>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={onRemove}
          disabled={!canRemove}
          aria-label={`Remove line ${n}`}
          leadingIcon={<MinusCircleIcon className="size-4" />}
        >
          Remove
        </Button>
      </div>

      <MovementItemPicker
        label={`Item (line ${n})`}
        value={line.itemOption}
        onChange={(option) =>
          onChange((previous) => ({
            ...previous,
            itemOption: option,
            values: { ...previous.values, inventoryItemId: option?.id ?? "" },
          }))
        }
        onPick={pick}
        {...errorProp(errors.item, serverErrors.inventory_item_id)}
      />

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Select
          label={`Take from bin (line ${n})`}
          value={values.fromLocationId}
          onChange={(e) => setValues({ fromLocationId: e.target.value })}
          options={binOptions}
          placeholder={!sourceChosen ? "Choose the source warehouse first" : binsLoading ? "Loading bins…" : "Choose a bin"}
          disabled={!sourceChosen || binsLoading}
          required
          {...errorProp(errors.location, serverErrors.from_location_id)}
        />
        <TextField
          label={`Quantity (line ${n})`}
          inputMode="decimal"
          value={values.quantity}
          onChange={(e) => setValues({ quantity: e.target.value })}
          helper={values.uom !== "" ? `In ${values.uom}, up to 3 decimals.` : "Up to 3 decimals."}
          required
          {...errorProp(errors.quantity, serverErrors.quantity)}
        />
      </div>

      <TrackingFields
        tracksLot={line.tracksLot}
        tracksRoll={line.tracksRoll}
        lotId={values.lotId}
        rollId={values.rollId}
        onLotChange={(lotId) => setValues({ lotId })}
        onRollChange={(rollId) => setValues({ rollId })}
        lotError={errors.lot ?? serverErrors.lot_id}
        rollError={errors.roll ?? serverErrors.roll_id}
        suffix={` (line ${n})`}
      />

      {errors.line !== undefined || serverErrors.line !== undefined ? (
        <p role="alert" className="text-sm text-negative">
          {errors.line ?? serverErrors.line}
        </p>
      ) : null}
    </fieldset>
  );
}
