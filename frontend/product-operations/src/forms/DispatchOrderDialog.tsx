import { useState } from "react";

import { dispatchOrder, listChannels, listOrders } from "@/api/client";
import type { CarrierBookingIn, ChannelOut, OrderOut, ShipmentIn, ShipmentOut } from "@/api/types";
import { useAsync, useMutation } from "@opsgrids/client";
import { formDialogError } from "@/lib/formError";
import { FormDialog, Select, TextField } from "@/ui";

type BookingField =
  | "consignee_name"
  | "consignee_phone"
  | "consignee_email"
  | "consignee_address"
  | "consignee_city"
  | "consignee_state"
  | "destination_pincode"
  | "weight_grams"
  | "cod_amount";

const EMPTY_BOOKING: Record<BookingField, string> = {
  consignee_name: "",
  consignee_phone: "",
  consignee_email: "",
  consignee_address: "",
  consignee_city: "",
  consignee_state: "",
  destination_pincode: "",
  weight_grams: "",
  cod_amount: "",
};

const BOOKING_FIELDS: { key: BookingField; label: string; max: number; numeric?: boolean; optional?: boolean }[] = [
  { key: "consignee_name", label: "Recipient name", max: 120 },
  { key: "consignee_phone", label: "Recipient phone", max: 16, numeric: true },
  { key: "consignee_email", label: "Recipient email", max: 120, optional: true },
  { key: "consignee_address", label: "Address", max: 300 },
  { key: "consignee_city", label: "City", max: 80 },
  { key: "consignee_state", label: "State", max: 80 },
  { key: "destination_pincode", label: "Pincode", max: 6, numeric: true },
  { key: "weight_grams", label: "Weight (grams)", max: 6, numeric: true },
  { key: "cod_amount", label: "Cash to collect (₹, 0 if prepaid)", max: 9, numeric: true, optional: true },
];

/** The booking payload, `null` when nothing was entered, `"incomplete"` when only some was. Money
 *  is typed in rupees and sent as integer paise. */
function bookingFrom(b: Record<BookingField, string>): CarrierBookingIn | null | "incomplete" {
  const required: BookingField[] = [
    "consignee_name",
    "consignee_phone",
    "consignee_address",
    "consignee_city",
    "consignee_state",
    "destination_pincode",
    "weight_grams",
  ];
  const any = Object.values(b).some((v) => v.trim() !== "");
  if (!any) return null;
  if (required.some((k) => b[k].trim() === "")) return "incomplete";
  const rupees = Number(b.cod_amount.trim() || "0");
  const grams = Number(b.weight_grams);
  if (!Number.isFinite(rupees) || rupees < 0 || !Number.isInteger(grams) || grams < 1) return "incomplete";
  return {
    consignee_name: b.consignee_name.trim(),
    consignee_phone: b.consignee_phone.trim(),
    ...(b.consignee_email.trim() ? { consignee_email: b.consignee_email.trim() } : {}),
    consignee_address: b.consignee_address.trim(),
    consignee_city: b.consignee_city.trim(),
    consignee_state: b.consignee_state.trim(),
    destination_pincode: b.destination_pincode.trim(),
    weight_grams: grams,
    cod_amount_minor: Math.round(rupees * 100),
  };
}

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
 *
 * **Carrier picker.** "Enter manually" is the original free-text carrier. Choosing a connected
 * courier (`GET /operations/channels?kind=carrier`) names it by connection and reveals the booking
 * details the courier needs (address, pincode, weight). When live booking is on the server books
 * with the courier and records the AWB; otherwise it only logs and records the tracking reference
 * typed here, so the details may be left empty. Either all the required details are filled or none.
 */
export function DispatchOrderDialog({ onClose, onDispatched }: DispatchOrderDialogProps) {
  const [orderId, setOrderId] = useState("");
  const [orderIdError, setOrderIdError] = useState<string | undefined>(undefined);
  const [carrierName, setCarrierName] = useState("");
  const [carrierNameError, setCarrierNameError] = useState<string | undefined>(undefined);
  const [trackingReference, setTrackingReference] = useState("");
  const [channelId, setChannelId] = useState("");
  const [booking, setBooking] = useState<Record<BookingField, string>>(EMPTY_BOOKING);
  const [bookingError, setBookingError] = useState<string | undefined>(undefined);

  const carriersState = useAsync(() => listChannels({ kind: "carrier", status: "connected", size: 100 }), []);
  const carriers: ChannelOut[] = carriersState.status === "success" ? carriersState.data.items : [];
  const carrierOptions = [
    { value: "", label: "Enter manually" },
    ...carriers.map((c) => ({ value: c.id, label: c.name })),
  ];

  const readyOrdersState = useAsync(() => listOrders({ status: "READY", size: 200 }), []);
  const readyOrders: OrderOut[] = readyOrdersState.status === "success" ? readyOrdersState.data.items : [];
  const options = readyOrders.map((o) => ({ value: o.id, label: `${o.number} — ${o.customer_name || "Walk-in"}` }));
  const selectedOrder = readyOrders.find((o) => o.id === orderId) ?? null;

  const dispatch = useMutation(
    (input: ShipmentIn) => dispatchOrder(input),
    { onSuccess: (saved) => onDispatched(saved) },
  );

  function submit() {
    setOrderIdError(undefined);
    setCarrierNameError(undefined);
    setBookingError(undefined);
    if (!selectedOrder) {
      setOrderIdError("Choose a ready order to hand over.");
      return;
    }
    if (!channelId && carrierName.trim().length < 1) {
      setCarrierNameError("Name the carrier this order is going with.");
      return;
    }
    const details = channelId ? bookingFrom(booking) : null;
    if (details === "incomplete") {
      setBookingError("Fill every booking detail, or leave them all empty.");
      return;
    }
    void dispatch.mutate({
      order_id: selectedOrder.id,
      order_version: selectedOrder.version,
      ...(channelId ? { channel_id: channelId } : { carrier_name: carrierName.trim() }),
      ...(trackingReference.trim() ? { tracking_reference: trackingReference.trim() } : {}),
      ...(details ? { booking: details } : {}),
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
      {carriers.length > 0 ? (
        <Select
          label="Carrier connection"
          value={channelId}
          onChange={(e) => setChannelId(e.target.value)}
          options={carrierOptions}
        />
      ) : null}
      {channelId ? null : (
        <TextField
          label="Carrier"
          value={carrierName}
          onChange={(e) => setCarrierName(e.target.value)}
          placeholder="Acme Couriers"
          maxLength={120}
          required
          {...(carrierNameError !== undefined ? { error: carrierNameError } : {})}
        />
      )}
      <TextField
        label="Tracking reference"
        optional
        value={trackingReference}
        onChange={(e) => setTrackingReference(e.target.value)}
        placeholder="A carrier tracking number, if known yet"
        maxLength={120}
      />
      {channelId ? (
        <fieldset className="flex flex-col gap-3">
          <legend className="text-sm font-semibold text-ink">Booking details</legend>
          <p className="text-sm text-muted">
            Sent to the courier when live booking is on. Leave empty to record the tracking reference above only.
          </p>
          {BOOKING_FIELDS.map((f) => (
            <TextField
              key={f.key}
              label={f.label}
              {...(f.optional ? { optional: true } : {})}
              value={booking[f.key]}
              onChange={(e) => setBooking((prev) => ({ ...prev, [f.key]: e.target.value }))}
              inputMode={f.numeric ? "numeric" : undefined}
              maxLength={f.max}
            />
          ))}
          {bookingError ? <p className="text-sm text-danger">{bookingError}</p> : null}
        </fieldset>
      ) : null}
    </FormDialog>
  );
}
