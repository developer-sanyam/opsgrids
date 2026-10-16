import { useState } from "react";

import { connectChannel, listWarehouses } from "@/api/client";
import type { ChannelOut, ChannelProvider, WarehouseOut } from "@/api/types";
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
  const [provider, setProvider] = useState<ChannelProvider>("shopify");
  const [webhookSecret, setWebhookSecret] = useState("");
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
      provider: ChannelProvider;
      name: string;
      external_shop_id: string;
      api_key: string;
      webhook_secret?: string;
      default_warehouse_id: string;
      auto_accept_orders: boolean;
    }) => connectChannel(input),
    { onSuccess: (saved) => onConnected(saved) },
  );

  const shopify = provider === "shopify";

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
      setShopIdError(
        shopify ? "Your store's address, like your-store.myshopify.com." : "Name the sandbox shop id this connects to.",
      );
      ok = false;
    }
    if (apiKey.trim().length < 1) {
      setApiKeyError(
        shopify ? "The Admin API access token of your custom app — sealed at rest." : "The shop's own API key — sealed at rest, never shown again.",
      );
      ok = false;
    }
    if (!warehouseId) {
      setWarehouseError("Choose where an imported order's stock is reserved from.");
      ok = false;
    }
    if (!ok) return;
    void connect.mutate({
      provider,
      name: name.trim(),
      ...(shopify && webhookSecret.trim() !== "" ? { webhook_secret: webhookSecret.trim() } : {}),
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
      description={
        shopify
          ? "Connect your own Shopify store with a custom app's Admin API access token (read orders, read and write inventory, write fulfilments)."
          : "Connects to this build's self-hosted sandbox marketplace."
      }
      submitLabel="Connect"
      pendingLabel="Connecting…"
      pending={connect.pending}
      {...formDialogError(connect.error, connect.fieldErrors)}
    >
      <Select
        label="Provider"
        value={provider}
        onChange={(e) => setProvider(e.target.value as ChannelProvider)}
        options={[
          { value: "shopify", label: "Shopify" },
          { value: "mock_marketplace", label: "Mock marketplace (sandbox)" },
        ]}
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
        label={shopify ? "Shop domain" : "Sandbox shop id"}
        value={externalShopId}
        onChange={(e) => setExternalShopId(e.target.value)}
        placeholder={shopify ? "your-store.myshopify.com" : "shop-1"}
        maxLength={120}
        required
        {...(shopIdError !== undefined ? { error: shopIdError } : {})}
      />
      <TextField
        label={shopify ? "Admin API access token" : "API key"}
        type="password"
        value={apiKey}
        onChange={(e) => setApiKey(e.target.value)}
        placeholder="Sealed at rest — never shown again after connecting"
        maxLength={500}
        required
        {...(apiKeyError !== undefined ? { error: apiKeyError } : {})}
      />
      {shopify ? (
        <TextField
          label="Webhook secret (optional)"
          type="password"
          value={webhookSecret}
          onChange={(e) => setWebhookSecret(e.target.value)}
          placeholder="The app's API secret key — needed to accept order webhooks"
          maxLength={200}
        />
      ) : null}
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
