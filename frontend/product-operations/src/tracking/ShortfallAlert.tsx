import { formatQty } from "@/lib/quantity";
import { describeReservation } from "@/lib/stockErrors";
import type { StockShortfall } from "@/lib/stockErrors";
import { InlineAlert } from "@/ui";

/**
 * The numbers behind a 409 `insufficient-stock`, and — when stock is there but promised — which
 * reservations are holding it (ADR-0014 §11). Renders nothing for a plain shortfall with no
 * reservations: the dialog's own error line already says it, and an empty "Reserved stock" panel
 * would be a claim nothing supports.
 */
export function ShortfallAlert({ shortfall }: { shortfall: StockShortfall | null }) {
  if (!shortfall || shortfall.reservations.length === 0) return null;
  const unit = shortfall.uom;
  return (
    <InlineAlert tone="warning" title="Reserved stock is in the way">
      <p>
        {formatQty(shortfall.on_hand)} {unit} on hand, {formatQty(shortfall.reserved)} {unit} reserved,{" "}
        {formatQty(shortfall.available)} {unit} available. These reservations hold it:
      </p>
      <ul className="mt-2 flex list-disc flex-col gap-1 pl-6">
        {shortfall.reservations.map((r) => (
          <li key={r.reservation_id} className="tnum">
            {describeReservation(r)} — {formatQty(r.quantity)} {unit}
          </li>
        ))}
      </ul>
    </InlineAlert>
  );
}
