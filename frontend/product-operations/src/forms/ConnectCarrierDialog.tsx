import { useState } from "react";

import { connectChannel, listWarehouses } from "@/api/client";
import type { ChannelOut, WarehouseOut } from "@/api/types";
import { useAsync, useMutation } from "@opsgrids/client";
import { formDialogError } from "@/lib/formError";
import { FormDialog, Select, TextField } from "@/ui";

interface ConnectCarrierDialogProps {
  onClose: () => void;
  onConnected: (channel: ChannelOut) => void;
}

/**
 * `POST /operations/channels` with `kind: "carrier"` — `operations.channels.channel.write:ORG`.
 * The business connects its **own** courier account; the platform books and tracks on it and never
 * holds the shipping money. Shiprocket signs in with the email and password of an API user created
 * in its panel (Settings → API → Create API user); both are sealed at rest and never shown again.
 * The pickup location must be named exactly as it is registered in the Shiprocket account.
 */
export function ConnectCarrierDialog({ onClose, onConnected }: ConnectCarrierDialogProps) {
  const [name, setName] = useState("");
  const [pickup, setPickup] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [warehouseId, setWarehouseId] = useState("");
  const [errors, setErrors] = useState<Record<string, string>>({});

  const warehousesState = useAsync(() => listWarehouses({ size: 200 }), []);
  const warehouses: WarehouseOut[] = warehousesState.status === "success" ? warehousesState.data.items : [];
  const warehouseOptions = warehouses.map((w) => ({ value: w.id, label: `${w.code} — ${w.name}` }));

  const connect = useMutation(connectChannel, { onSuccess: (saved) => onConnected(saved) });

  function submit() {
    const next: Record<string, string> = {};
    if (name.trim().length < 1) next.name = "Give this connection a name you'll recognise.";
    if (pickup.trim().length < 1) next.pickup = "Name the pickup location exactly as it appears in Shiprocket.";
    if (email.trim().length < 1) next.email = "The API user's email.";
    if (password.length < 1) next.password = "The API user's password — sealed at rest, never shown again.";
    if (!warehouseId) next.warehouse = "Choose the warehouse these shipments leave from.";
    setErrors(next);
    if (Object.keys(next).length > 0) return;
    void connect.mutate({
      provider: "shiprocket",
      kind: "carrier",
      name: name.trim(),
      external_shop_id: pickup.trim(),
      api_key: email.trim(),
      api_secret: password,
      default_warehouse_id: warehouseId,
      auto_accept_orders: false,
    });
  }

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title="Connect a courier"
      description="Connect your own courier account. Bookings and tracking run on it; shipping charges stay between you and the courier."
      submitLabel="Connect"
      pendingLabel="Connecting…"
      pending={connect.pending}
      {...formDialogError(connect.error, connect.fieldErrors)}
    >
      <Select
        label="Courier"
        value="shiprocket"
        onChange={() => {}}
        options={[{ value: "shiprocket", label: "Shiprocket" }]}
        disabled
      />
      <TextField
        label="Connection name"
        value={name}
        onChange={(e) => setName(e.target.value)}
        placeholder="Shiprocket — Pune"
        maxLength={120}
        required
        {...(errors.name !== undefined ? { error: errors.name } : {})}
      />
      <TextField
        label="Pickup location"
        value={pickup}
        onChange={(e) => setPickup(e.target.value)}
        placeholder="Primary"
        maxLength={120}
        required
        {...(errors.pickup !== undefined ? { error: errors.pickup } : {})}
      />
      <TextField
        label="API user email"
        type="email"
        value={email}
        onChange={(e) => setEmail(e.target.value)}
        maxLength={500}
        autoComplete="off"
        required
        {...(errors.email !== undefined ? { error: errors.email } : {})}
      />
      <TextField
        label="API user password"
        type="password"
        value={password}
        onChange={(e) => setPassword(e.target.value)}
        maxLength={200}
        autoComplete="new-password"
        required
        {...(errors.password !== undefined ? { error: errors.password } : {})}
      />
      <Select
        label="Warehouse"
        value={warehouseId}
        onChange={(e) => setWarehouseId(e.target.value)}
        options={warehouseOptions}
        placeholder={warehousesState.status === "loading" ? "Loading warehouses…" : "Where shipments leave from"}
        required
        disabled={warehousesState.status !== "success"}
        {...(errors.warehouse !== undefined ? { error: errors.warehouse } : {})}
      />
    </FormDialog>
  );
}
