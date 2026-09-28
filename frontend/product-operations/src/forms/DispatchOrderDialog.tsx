import { useState } from "react";

import { dispatchOrder, listOrders } from "@/api/client";
import type { OrderOut, ShipmentOut } from "@/api/types";
import { useAsync, useMutation } from "@opsgrids/client";
import { formDialogError } from "@/lib/formError";
import { FormDialog, Select, TextField } from "@/ui";

interface DispatchOrderDialogProps {
  onClose: () => void;
  onDispatched: (shipment: ShipmentOut) => void;
}

/**
 * `POST /operations/dispatch/shipments` — `operations.dispatch.shipment.write:ORG`. The carrier
 * handover (Fulfilment F5): creates the shipment and moves the order through the billing gate
 * (`app.orders.services.check_billing_gate`, the same gate the counter path enforces) in one
 * call. Only orders currently `READY` are offered — the same precondition the server itself
 * checks (`domain.can_transition_order(order.status, "HANDED_OVER")`), so a 409 here is the
 * billing gate itself refusing (a missing invoice, unpaid order, or unverified pickup code),
 * not a wrong choice of order.
 */
export function DispatchOrderDialog({ onClose, onDispatched }: DispatchOrderDialogProps) {
  const [orderId, setOrderId] = useState("");
  const [orderIdError, setOrderIdError] = useState<string | undefined>(undefined);
  const [carrierName, setCarrierName] = useState("");
  const [carrierNameError, setCarrierNameError] = useState<string | undefined>(undefined);
  const [trackingReference, setTrackingReference] = useState("");

  const readyOrdersState = useAsync(() => listOrders({ status: "READY", size: 200 }), []);
  const readyOrders: OrderOut[] = readyOrdersState.status === "success" ? readyOrdersState.data.items : [];
  const options = readyOrders.map((o) => ({ value: o.id, label: `${o.number} — ${o.customer_name || "Walk-in"}` }));
  const selectedOrder = readyOrders.find((o) => o.id === orderId) ?? null;

  const dispatch = useMutation(
    (input: { order_id: string; order_version: number; carrier_name: string; tracking_reference?: string }) =>
      dispatchOrder(input),
    { onSuccess: (saved) => onDispatched(saved) },
  );

  function submit() {
    setOrderIdError(undefined);
    setCarrierNameError(undefined);
    if (!selectedOrder) {
      setOrderIdError("Choose a ready order to hand over.");
      return;
    }
    if (carrierName.trim().length < 1) {
      setCarrierNameError("Name the carrier this order is going with.");
      return;
    }
    void dispatch.mutate({
      order_id: selectedOrder.id,
      order_version: selectedOrder.version,
      carrier_name: carrierName.trim(),
      ...(trackingReference.trim() ? { tracking_reference: trackingReference.trim() } : {}),
    });
  }

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title="Hand an order to a carrier"
      description="Only orders that are Ready are offered — the billing gate runs the moment you confirm."
      submitLabel="Confirm handover"
      pendingLabel="Handing over…"
      pending={dispatch.pending}
      {...formDialogError(dispatch.error, dispatch.fieldErrors)}
    >
      <Select
        label="Order"
        value={orderId}
        onChange={(e) => setOrderId(e.target.value)}
        options={options}
        placeholder={
          readyOrdersState.status === "loading"
            ? "Loading ready orders…"
            : options.length === 0
              ? "No orders are ready to dispatch"
              : "Choose an order"
        }
        required
        disabled={readyOrdersState.status !== "success" || options.length === 0}
        {...(orderIdError !== undefined ? { error: orderIdError } : {})}
      />
      <TextField
        label="Carrier"
        value={carrierName}
        onChange={(e) => setCarrierName(e.target.value)}
        placeholder="Acme Couriers"
        maxLength={120}
        required
        {...(carrierNameError !== undefined ? { error: carrierNameError } : {})}
      />
      <TextField
        label="Tracking reference"
        optional
        value={trackingReference}
        onChange={(e) => setTrackingReference(e.target.value)}
        placeholder="A carrier tracking number, if known yet"
        maxLength={120}
      />
    </FormDialog>
  );
}
