import { describeBlocker, describeRefusal } from "@/lib/stockRefusal";
import type { StockRefusal } from "@/lib/stockRefusal";
import { InlineAlert } from "@/ui";

interface StockRefusalAlertProps {
  refusals: StockRefusal[];
  /** What was being attempted, for the headline: "reserve this stock". */
  action: string;
}

/**
 * A 409 `insufficient-stock`, with its numbers in plain words: what was asked for, what is
 * available, what is already on hand and reserved — and the holds standing in the way, so the
 * fix ("release that reservation, then try again") is visible, not guessed at.
 */
export function StockRefusalAlert({ refusals, action }: StockRefusalAlertProps) {
  if (refusals.length === 0) return null;
  return (
    <InlineAlert tone="danger" title={`Not enough stock to ${action}`}>
      <ul className="mt-1 flex flex-col gap-2">
        {refusals.map((refusal, index) => (
          <li key={`${refusal.sku}-${refusal.location}-${index}`} className="tnum">
            {describeRefusal(refusal)}
            {refusal.reservations.length > 0 ? (
              <>
                {" "}
                Held by: {refusal.reservations.map((r) => describeBlocker(r, refusal.uom)).join("; ")}. Release a
                hold, then try again.
              </>
            ) : null}
          </li>
        ))}
      </ul>
    </InlineAlert>
  );
}
