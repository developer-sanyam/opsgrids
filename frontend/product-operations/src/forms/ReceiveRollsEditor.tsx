import { emptyRollDraft, nextRollKey } from "@/lib/receiptForm";
import type { LineErrors, RollDraft } from "@/lib/receiptForm";
import { formatQty, milliToQty } from "@/lib/quantity";
import { Button, TextField } from "@/ui";
import { MinusCircleIcon, PlusIcon } from "@opsgrids/ui";

interface ReceiveRollsEditorProps {
  rolls: RollDraft[];
  onChange: (rolls: RollDraft[]) => void;
  /** The item's stock unit: a roll's length is in it. */
  stockUom: string;
  /** What the rolls must add up to, in milli-units of the stock unit, when the line quantity is valid. */
  targetMilli: number | null;
  rollsMilli: number;
  errors: LineErrors;
  /** Whether to show errors that are only about an empty or unfinished set. */
  showErrors: boolean;
}

/**
 * The physical rolls in one line's delivery. `length` is the roll's quantity in the stock unit and
 * is what has to add up to the line quantity; the measured weight is a separate figure in its own
 * unit and is never derived from it.
 */
export function ReceiveRollsEditor({
  rolls,
  onChange,
  stockUom,
  targetMilli,
  rollsMilli,
  errors,
  showErrors,
}: ReceiveRollsEditorProps) {
  const update = (key: string, patch: Partial<RollDraft>) =>
    onChange(rolls.map((r) => (r.key === key ? { ...r, ...patch } : r)));

  const matches = targetMilli !== null && rollsMilli === targetMilli;

  return (
    <fieldset className="flex flex-col gap-3 rounded-tile border border-line p-4">
      <legend className="px-1 text-sm font-bold text-ink">Rolls</legend>

      {rolls.map((roll, index) => {
        const e = errors.roll[roll.key] ?? {};
        return (
          <div
            key={roll.key}
            role="group"
            aria-label={`Roll ${index + 1}`}
            className="flex flex-col gap-3 rounded-tile bg-ground p-3"
          >
            <div className="flex items-center justify-between gap-2">
              <span className="text-xs font-semibold uppercase tracking-wide text-faint">Roll {index + 1}</span>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                leadingIcon={<MinusCircleIcon className="size-4" />}
                onClick={() => onChange(rolls.filter((r) => r.key !== roll.key))}
              >
                Remove roll {index + 1}
              </Button>
            </div>
            <div className="grid grid-cols-1 gap-3 min-[400px]:grid-cols-2 lg:grid-cols-4">
              <TextField
                label="Roll number"
                value={roll.rollNo}
                onChange={(ev) => update(roll.key, { rollNo: ev.target.value })}
                optional
                maxLength={60}
                helper="Blank generates one."
                {...(showErrors && e.rollNo !== undefined ? { error: e.rollNo } : {})}
              />
              <TextField
                label={`Length (${stockUom})`}
                inputMode="decimal"
                value={roll.primary}
                onChange={(ev) => update(roll.key, { primary: ev.target.value })}
                required
                {...(showErrors && e.primary !== undefined ? { error: e.primary } : {})}
              />
              <TextField
                label="Measured weight"
                inputMode="decimal"
                value={roll.secondary}
                onChange={(ev) => update(roll.key, { secondary: ev.target.value })}
                optional
                {...(showErrors && e.secondary !== undefined ? { error: e.secondary } : {})}
              />
              <TextField
                label="Weight unit"
                value={roll.secondaryUom}
                onChange={(ev) => update(roll.key, { secondaryUom: ev.target.value })}
                optional
                maxLength={10}
                placeholder="kg"
                {...(showErrors && e.secondaryUom !== undefined ? { error: e.secondaryUom } : {})}
              />
              <TextField
                label="Width (mm)"
                inputMode="numeric"
                value={roll.widthMm}
                onChange={(ev) => update(roll.key, { widthMm: ev.target.value })}
                optional
                {...(showErrors && e.widthMm !== undefined ? { error: e.widthMm } : {})}
              />
              <TextField
                label="GSM"
                inputMode="numeric"
                value={roll.gsm}
                onChange={(ev) => update(roll.key, { gsm: ev.target.value })}
                optional
                {...(showErrors && e.gsm !== undefined ? { error: e.gsm } : {})}
              />
              <TextField
                label="Shade"
                value={roll.shade}
                onChange={(ev) => update(roll.key, { shade: ev.target.value })}
                optional
                maxLength={40}
              />
              <TextField
                label="Grade"
                value={roll.grade}
                onChange={(ev) => update(roll.key, { grade: ev.target.value })}
                optional
                maxLength={20}
              />
            </div>
          </div>
        );
      })}

      <div className="flex flex-wrap items-center justify-between gap-3">
        <Button
          type="button"
          variant="outline"
          size="sm"
          leadingIcon={<PlusIcon className="size-4" />}
          disabled={rolls.length >= 500}
          onClick={() => onChange([...rolls, emptyRollDraft(nextRollKey())])}
        >
          Add roll
        </Button>
        <p className="text-sm tnum text-ink-soft" aria-live="polite">
          Rolls total {formatQty(milliToQty(rollsMilli))} {stockUom}
          {targetMilli !== null ? (
            <>
              {" "}of {formatQty(milliToQty(targetMilli))} {stockUom}
              {matches ? ". They add up." : ". They don't add up yet."}
            </>
          ) : null}
        </p>
      </div>
      {errors.rolls !== undefined && (showErrors || rolls.length > 0) ? (
        <p role="alert" className="text-sm font-semibold text-negative">
          {errors.rolls}
        </p>
      ) : null}
    </fieldset>
  );
}
