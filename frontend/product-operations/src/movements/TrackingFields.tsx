import { TextField } from "@/ui";

interface TrackingFieldsProps {
  /** The item is tracked by lot / by roll — the id field is then asked for outright. */
  tracksLot: boolean;
  tracksRoll: boolean;
  lotId: string;
  rollId: string;
  onLotChange: (value: string) => void;
  onRollChange: (value: string) => void;
  lotError?: string | undefined;
  rollError?: string | undefined;
  /** Distinguishes two rows of the same form in the accessible names. */
  suffix?: string;
}

/**
 * The lot and roll ids a stock movement may name. Shown only for an item tracked by lot or roll
 * (or when the row already carries one); an untracked item has no lot to name and the fields stay
 * out of the way. The id is the lot's or roll's own UUID — the lots screen is where it is found.
 */
export function TrackingFields({
  tracksLot,
  tracksRoll,
  lotId,
  rollId,
  onLotChange,
  onRollChange,
  lotError,
  rollError,
  suffix = "",
}: TrackingFieldsProps) {
  const showLot = tracksLot || lotId !== "";
  const showRoll = tracksRoll || rollId !== "";
  if (!showLot && !showRoll) return null;
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
      {showLot ? (
        <TextField
          label={`Lot id${suffix}`}
          value={lotId}
          onChange={(e) => onLotChange(e.target.value)}
          optional
          helper="The lot this stock belongs to."
          {...(lotError !== undefined ? { error: lotError } : {})}
        />
      ) : null}
      {showRoll ? (
        <TextField
          label={`Roll id${suffix}`}
          value={rollId}
          onChange={(e) => onRollChange(e.target.value)}
          optional
          helper="The roll this stock belongs to."
          {...(rollError !== undefined ? { error: rollError } : {})}
        />
      ) : null}
    </div>
  );
}
