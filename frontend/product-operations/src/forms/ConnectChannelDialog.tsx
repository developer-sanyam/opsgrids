import { useState } from "react";

import { connectChannel, listWarehouses } from "@/api/client";
import type { ChannelOut, WarehouseOut } from "@/api/types";
import { useAsync, useMutation } from "@opsgrids/client";
import { formDialogError } from "@/lib/formError";
import { FormDialog, Select, Switch, TextField } from "@/ui";

interface ConnectChannelDialogProps {
  onClose: () => void;
  onConnected: (channel: ChannelOut) => void;
}

/**
 * `POST /operations/channels` — `operations.channels.channel.write:ORG` (Fulfilment F11). Only
 * `provider: "mock_marketplace"` exists in this build — a self-hosted sandbox marketplace
 * (`bin/mock_marketplace`) standing in for a real Shopify/Amazon account this environment has no
 * credentials for. `external_shop_id` names which sandbox shop to connect to (the id the sandbox
 * was seeded with, or a `curl -X POST http://localhost:8811/_admin/shops` call created).
 */
export function ConnectChannelDialog({ onClose, onConnected }: ConnectChannelDialogProps) {
  const [name, setName] = useState("");
  const [nameError, setNameError] = useState<string | undefined>(undefined);
  const [externalShopId, setExternalShopId] = useState("");
  const [shopIdError, setShopIdError] = useState<string | undefined>(undefined);
  const [apiKey, setApiKey] = useState("");
  const [apiKeyError, setApiKeyError] = useState<string | undefined>(undefined);
  const [warehouseId, setWarehouseId] = useState("");
  const [warehouseError, setWarehouseError] = useState<string | undefined>(undefined);
  const [autoAccept, setAutoAccept] = useState(true);

  const warehousesState = useAsync(() => listWarehouses({ size: 200 }), []);
  const warehouses: WarehouseOut[] = warehousesState.status === "success" ? warehousesState.data.items : [];
  const warehouseOptions = warehouses.map((w) => ({ value: w.id, label: `${w.code} — ${w.name}` }));

  const connect = useMutation(
    (input: {
      provider: "mock_marketplace";
      name: string;
      external_shop_id: string;
      api_key: string;
      default_warehouse_id: string;
      auto_accept_orders: boolean;
    }) => connectChannel(input),
    { onSuccess: (saved) => onConnected(saved) },
  );

  function submit() {
    setNameError(undefined);
    setShopIdError(undefined);
    setApiKeyError(undefined);
    setWarehouseError(undefined);
    let ok = true;
    if (name.trim().length < 1) {
      setNameError("Give this connection a name you'll recognise.");
      ok = false;
    }
    if (externalShopId.trim().length < 1) {
      setShopIdError("Name the sandbox shop id this connects to.");
      ok = false;
    }
    if (apiKey.trim().length < 1) {
      setApiKeyError("The shop's own API key — sealed at rest, never shown again.");
      ok = false;
    }
    if (!warehouseId) {
      setWarehouseError("Choose where an imported order's stock is reserved from.");
      ok = false;
    }
    if (!ok) return;
    void connect.mutate({
      provider: "mock_marketplace",
      name: name.trim(),
      external_shop_id: externalShopId.trim(),
      api_key: apiKey.trim(),
      default_warehouse_id: warehouseId,
      auto_accept_orders: autoAccept,
    });
  }

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title="Connect a channel"
      description="Connects to this build's self-hosted sandbox marketplace — the real connector, proven end to end with no real Shopify/Amazon account available in this environment."
      submitLabel="Connect"
      pendingLabel="Connecting…"
      pending={connect.pending}
      {...formDialogError(connect.error, connect.fieldErrors)}
    >
      <Select
        label="Provider"
        value="mock_marketplace"
        onChange={() => {}}
        options={[{ value: "mock_marketplace", label: "Mock marketplace (sandbox)" }]}
        disabled
      />
      <TextField
        label="Connection name"
        value={name}
        onChange={(e) => setName(e.target.value)}
        placeholder="My Shopify-like store"
        maxLength={120}
        required
        {...(nameError !== undefined ? { error: nameError } : {})}
      />
      <TextField
        label="Sandbox shop id"
        value={externalShopId}
        onChange={(e) => setExternalShopId(e.target.value)}
        placeholder="shop-1"
        maxLength={120}
        required
        {...(shopIdError !== undefined ? { error: shopIdError } : {})}
      />
      <TextField
        label="API key"
        type="password"
        value={apiKey}
        onChange={(e) => setApiKey(e.target.value)}
        placeholder="Sealed at rest — never shown again after connecting"
        maxLength={500}
        required
        {...(apiKeyError !== undefined ? { error: apiKeyError } : {})}
      />
      <Select
        label="Default warehouse"
        value={warehouseId}
        onChange={(e) => setWarehouseId(e.target.value)}
        options={warehouseOptions}
        placeholder={warehousesState.status === "loading" ? "Loading warehouses…" : "Where imported orders reserve stock"}
        required
        disabled={warehousesState.status !== "success"}
        {...(warehouseError !== undefined ? { error: warehouseError } : {})}
      />
      <Switch
        label="Auto-accept imported orders"
        description="Reserves stock immediately on import. Off leaves each order Placed for a person to accept."
        checked={autoAccept}
        onChange={setAutoAccept}
      />
    </FormDialog>
  );
}
