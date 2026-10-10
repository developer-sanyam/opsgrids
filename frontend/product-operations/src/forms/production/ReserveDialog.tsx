import { reserveProductionMaterials } from "@/api/client";
import type { ProductionActionOut, ProductionOrderDetailOut, ReserveIn } from "@/api/types";
import { useOrderAction } from "@/forms/production/useOrderAction";
import { problemMessage } from "@opsgrids/client";
import { ConfirmDialog } from "@/ui";

interface ReserveDialogProps {
  order: ProductionOrderDetailOut;
  onClose: () => void;
  onDone: (result: ProductionActionOut) => void;
}

/**
 * `POST .../orders/{id}/reserve` — `...production_order.issue`. Reserves every material's open need
 * from whatever available stock there is. A short reservation is not an error: the response lists
 * the shortages and the screen shows them in a "Material shortage" banner.
 */
export function ReserveDialog({ order, onClose, onDone }: ReserveDialogProps) {
  const reserve = useOrderAction<ReserveIn>((input, options) => reserveProductionMaterials(order.id, input, options), onDone);
  return (
    <ConfirmDialog
      open
      onClose={() => {
        reserve.reset();
        onClose();
      }}
      onConfirm={() => reserve.mutate({})}
      title={`Reserve material for ${order.number}?`}
      body="Reserves the open need of every material from available stock. Anything that can't be covered is listed afterwards as a shortage."
      confirmLabel="Reserve material"
      pendingLabel="Reserving…"
      tone="primary"
      pending={reserve.pending}
      {...(reserve.error ? { error: problemMessage(reserve.error) } : {})}
    />
  );
}
