import { useState } from "react";

import { cancelProductionOrder } from "@/api/client";
import type { ProductionActionOut, ProductionCancelIn, ProductionOrderDetailOut } from "@/api/types";
import { useOrderAction } from "@/forms/production/useOrderAction";
import { problemMessage } from "@opsgrids/client";
import { ConfirmDialog, TextArea } from "@/ui";

interface CancelOrderDialogProps {
  order: ProductionOrderDetailOut;
  onClose: () => void;
  onDone: (result: ProductionActionOut) => void;
}

/**
 * `POST .../orders/{id}/cancel` — `...production_order.write`. Releases the order's reservations;
 * refused (409, shown verbatim) while material is still in WIP.
 */
export function CancelOrderDialog({ order, onClose, onDone }: CancelOrderDialogProps) {
  const [reason, setReason] = useState("");
  const cancel = useOrderAction<ProductionCancelIn>((input, options) => cancelProductionOrder(order.id, input, options), onDone);

  return (
    <ConfirmDialog
      open
      onClose={() => {
        cancel.reset();
        onClose();
      }}
      onConfirm={() => cancel.mutate({ reason: reason.trim() })}
      title={`Cancel ${order.number}?`}
      body="Its reservations are released and the order can't be restarted. Material already issued to work in progress must be returned or written off first."
      confirmLabel="Cancel order"
      pendingLabel="Cancelling…"
      tone="danger"
      pending={cancel.pending}
      {...(cancel.error ? { error: problemMessage(cancel.error) } : {})}
    >
      <TextArea
        label="Reason"
        rows={2}
        value={reason}
        onChange={(e) => setReason(e.target.value)}
        optional
        maxLength={500}
        counterFrom={400}
        error={cancel.fieldErrors.reason}
      />
    </ConfirmDialog>
  );
}
