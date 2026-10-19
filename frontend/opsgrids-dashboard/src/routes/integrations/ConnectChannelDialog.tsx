import { useState } from "react";

import { useMutation } from "@opsgrids/client";
import { FormDialog, InlineAlert, Select, Switch, TextField } from "@opsgrids/ui";

import { connectChannel } from "@/api/integrations";
import type { ChannelConnectIn, ChannelKind, ChannelOut, WarehouseOut } from "@/api/integrations";
import type { Connector } from "@/lib/integrations";

interface ConnectChannelDialogProps {
  connector: Connector;
  warehouses: WarehouseOut[];
  onClose: () => void;
  onConnected: (channel: ChannelOut) => void;
}

export function ConnectChannelDialog({
  connector,
  warehouses,
  onClose,
  onConnected,
}: ConnectChannelDialogProps) {
  const isCarrier = connector.category === "couriers";
  const kind: ChannelKind = isCarrier
    ? "carrier"
    : connector.category === "pos"
      ? "pos"
      : connector.category === "erp"
        ? "erp"
        : "sales";

  const isShopify = connector.key === "shopify";
  const isShiprocket = connector.key === "shiprocket" || connector.key === "shiprocket_hyperlocal";
  const isDelhivery = connector.key === "delhivery" || connector.key === "delhivery_hyperlocal";

  const [name, setName] = useState(`${connector.name} Connection`);
  const [nameError, setNameError] = useState<string | undefined>();
  const [shopId, setShopId] = useState("");
  const [shopIdError, setShopIdError] = useState<string | undefined>();
  const [apiKey, setApiKey] = useState("");
  const [apiKeyError, setApiKeyError] = useState<string | undefined>();
  const [apiSecret, setApiSecret] = useState("");
  const [webhookSecret, setWebhookSecret] = useState("");
  const [warehouseId, setWarehouseId] = useState(warehouses[0]?.id ?? "");
  const [warehouseError, setWarehouseError] = useState<string | undefined>();
  const [autoAccept, setAutoAccept] = useState(true);

  const warehouseOptions = warehouses.map((w) => ({
    value: w.id,
    label: `${w.code} — ${w.name}`,
  }));

  const connect = useMutation(
    (input: ChannelConnectIn) => connectChannel(input),
    {
      onSuccess: (saved) => onConnected(saved),
    },
  );

  function submit() {
    setNameError(undefined);
    setShopIdError(undefined);
    setApiKeyError(undefined);
    setWarehouseError(undefined);

    let ok = true;
    if (!name.trim()) {
      setNameError("Please give this connection a recognizable name.");
      ok = false;
    }
    if (!shopId.trim()) {
      setShopIdError(
        isCarrier
          ? "Please provide the pickup location name registered with the courier."
          : isShopify
            ? "Your store domain, e.g. your-store.myshopify.com."
            : "Store or Account identifier.",
      );
      ok = false;
    }
    if (!apiKey.trim()) {
      setApiKeyError(
        isShiprocket
          ? "API user email address."
          : isDelhivery
            ? "API Token from Delhivery One panel."
            : isShopify
              ? "Admin API access token (shpat_...)."
              : "API Key or Access Token.",
      );
      ok = false;
    }
    if (!warehouseId && warehouses.length > 0) {
      setWarehouseError("Please choose the warehouse associated with this channel.");
      ok = false;
    }

    if (!ok) return;

    void connect.mutate({
      provider: connector.key,
      kind,
      name: name.trim(),
      external_shop_id: shopId.trim(),
      api_key: apiKey.trim(),
      ...(apiSecret.trim() ? { api_secret: apiSecret.trim() } : {}),
      ...(webhookSecret.trim() ? { webhook_secret: webhookSecret.trim() } : {}),
      default_warehouse_id: warehouseId || "00000000-0000-0000-0000-000000000001",
      auto_accept_orders: autoAccept,
    });
  }

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title={`Connect ${connector.name}`}
      description={
        connector.description ??
        `Configure your ${connector.name} account to enable automated syncing across all products in your organization.`
      }
      submitLabel="Connect Integration"
      pendingLabel="Connecting…"
      pending={connect.pending}
    >
      {connect.error ? (
        <InlineAlert tone="danger" title="Connection failed">
          {connect.error.message || "Could not connect to provider. Please verify your credentials."}
        </InlineAlert>
      ) : null}

      <TextField
        label="Connection name"
        value={name}
        onChange={(e) => setName(e.target.value)}
        placeholder={`e.g. ${connector.name} Primary`}
        maxLength={120}
        required
        {...(nameError ? { error: nameError } : {})}
      />

      <TextField
        label={
          isCarrier
            ? "Registered pickup location"
            : isShopify
              ? "Shop domain"
              : "Shop ID / Account identifier"
        }
        value={shopId}
        onChange={(e) => setShopId(e.target.value)}
        placeholder={
          isCarrier
            ? "e.g. Primary Warehouse"
            : isShopify
              ? "your-store.myshopify.com"
              : "e.g. shop-1 or account id"
        }
        maxLength={120}
        required
        {...(shopIdError ? { error: shopIdError } : {})}
      />

      <TextField
        label={
          isShiprocket
            ? "API user email"
            : isDelhivery
              ? "Delhivery API token"
              : isShopify
                ? "Admin API access token"
                : "API key / token"
        }
        type={isShiprocket ? "email" : "password"}
        value={apiKey}
        onChange={(e) => setApiKey(e.target.value)}
        placeholder="Sealed securely at rest — never exposed after saving"
        maxLength={500}
        required
        {...(apiKeyError ? { error: apiKeyError } : {})}
      />

      {isShiprocket ? (
        <TextField
          label="API user password"
          type="password"
          value={apiSecret}
          onChange={(e) => setApiSecret(e.target.value)}
          placeholder="Password for the Shiprocket API user"
          maxLength={200}
          required
        />
      ) : null}

      {isShopify ? (
        <TextField
          label="Webhook secret key (optional)"
          type="password"
          value={webhookSecret}
          onChange={(e) => setWebhookSecret(e.target.value)}
          placeholder="App's API secret key to verify incoming order webhooks"
          maxLength={200}
        />
      ) : null}

      {warehouseOptions.length > 0 ? (
        <Select
          label="Assigned warehouse"
          value={warehouseId}
          onChange={(e) => setWarehouseId(e.target.value)}
          options={warehouseOptions}
          placeholder="Select warehouse for reservations & stock"
          required
          {...(warehouseError ? { error: warehouseError } : {})}
        />
      ) : null}

      {!isCarrier ? (
        <Switch
          label="Auto-accept imported orders"
          description="Automatically confirms and reserves stock as soon as orders arrive from this channel."
          checked={autoAccept}
          onChange={setAutoAccept}
        />
      ) : null}
    </FormDialog>
  );
}
