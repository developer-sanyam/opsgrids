import { useState } from "react";

import { useMutation } from "@opsgrids/client";
import { Button, Dialog, InlineAlert, Select, StatusBadge, Switch, TextField } from "@opsgrids/ui";

import {
  checkChannelHealth,
  disconnectChannel,
  reconnectChannel,
  syncChannelInventory,
  syncChannelOrders,
  updateChannelConfig,
} from "@/api/integrations";
import type { ChannelOut, WarehouseOut } from "@/api/integrations";

interface ManageChannelDialogProps {
  channel: ChannelOut;
  warehouses: WarehouseOut[];
  onClose: () => void;
  onUpdated: (channel: ChannelOut) => void;
  onDisconnected: (channelId: string) => void;
}

export function ManageChannelDialog({
  channel,
  warehouses,
  onClose,
  onUpdated,
  onDisconnected,
}: ManageChannelDialogProps) {
  const isCarrier = channel.kind === "carrier";

  const [name, setName] = useState(channel.name);
  const [warehouseId, setWarehouseId] = useState(channel.default_warehouse_id ?? "");
  const [autoAccept, setAutoAccept] = useState(channel.auto_accept_orders);
  const [feedback, setFeedback] = useState<{ tone: "success" | "danger"; message: string } | null>(null);

  // Reconnect state
  const [showRotateKey, setShowRotateKey] = useState(false);
  const [newApiKey, setNewApiKey] = useState("");
  const [newWebhookSecret, setNewWebhookSecret] = useState("");

  const warehouseOptions = warehouses.map((w) => ({
    value: w.id,
    label: `${w.code} — ${w.name}`,
  }));

  const saveConfig = useMutation(
    () =>
      updateChannelConfig(channel.id, {
        name: name.trim(),
        default_warehouse_id: warehouseId || undefined,
        auto_accept_orders: autoAccept,
      }),
    {
      onSuccess: (updated) => {
        setFeedback({ tone: "success", message: "Channel configuration updated." });
        onUpdated(updated);
      },
      onError: (err) => {
        setFeedback({ tone: "danger", message: err.message || "Failed to update configuration." });
      },
    },
  );

  const rotateKey = useMutation(
    () =>
      reconnectChannel(channel.id, {
        api_key: newApiKey.trim(),
        ...(newWebhookSecret.trim() ? { webhook_secret: newWebhookSecret.trim() } : {}),
      }),
    {
      onSuccess: (updated) => {
        setShowRotateKey(false);
        setNewApiKey("");
        setNewWebhookSecret("");
        setFeedback({ tone: "success", message: "API credentials rotated and connection re-established." });
        onUpdated(updated);
      },
      onError: (err) => {
        setFeedback({ tone: "danger", message: err.message || "Failed to rotate credentials." });
      },
    },
  );

  const runSyncOrders = useMutation(
    () => syncChannelOrders(channel.id),
    {
      onSuccess: (res) => {
        setFeedback({
          tone: res.status === "success" ? "success" : "danger",
          message: `Order sync ${res.status}: ${res.items_processed} orders processed.${res.error_message ? ` (${res.error_message})` : ""}`,
        });
      },
      onError: (err) => setFeedback({ tone: "danger", message: `Order sync failed: ${err.message}` }),
    },
  );

  const runSyncInventory = useMutation(
    () => syncChannelInventory(channel.id),
    {
      onSuccess: (res) => {
        setFeedback({
          tone: res.status === "success" ? "success" : "danger",
          message: `Inventory sync ${res.status}: ${res.items_processed} items synced.`,
        });
      },
      onError: (err) => setFeedback({ tone: "danger", message: `Inventory sync failed: ${err.message}` }),
    },
  );

  const runHealthCheck = useMutation(
    () => checkChannelHealth(channel.id),
    {
      onSuccess: (res) => {
        setFeedback({
          tone: res.healthy ? "success" : "danger",
          message: `Health check: ${res.detail}`,
        });
      },
      onError: (err) => setFeedback({ tone: "danger", message: `Health check failed: ${err.message}` }),
    },
  );

  const disconnect = useMutation(
    () => disconnectChannel(channel.id),
    {
      onSuccess: () => {
        onDisconnected(channel.id);
        onClose();
      },
      onError: (err) => setFeedback({ tone: "danger", message: `Disconnect failed: ${err.message}` }),
    },
  );

  return (
    <Dialog open onClose={onClose} title={`Manage ${channel.name}`}>
      <div className="flex flex-col gap-6">
        {feedback ? (
          <InlineAlert tone={feedback.tone} title={feedback.tone === "success" ? "Success" : "Error"}>
            {feedback.message}
          </InlineAlert>
        ) : null}

        <div className="flex flex-wrap items-center justify-between gap-3 rounded-card bg-surface-muted p-4">
          <div className="flex flex-col gap-1">
            <span className="text-xs font-semibold uppercase tracking-wider text-muted">Provider</span>
            <span className="text-base font-bold capitalize">{channel.provider.replace("_", " ")}</span>
          </div>
          <div className="flex flex-col gap-1">
            <span className="text-xs font-semibold uppercase tracking-wider text-muted">
              {isCarrier ? "Pickup Location" : "Shop / Account ID"}
            </span>
            <span className="text-sm font-medium">{channel.external_shop_id || "None"}</span>
          </div>
          <div className="flex flex-col gap-1">
            <span className="text-xs font-semibold uppercase tracking-wider text-muted">Status</span>
            <StatusBadge
              tone={channel.status === "connected" ? "success" : channel.status === "error" ? "danger" : "neutral"}
              label={channel.status}
            />
          </div>
          <div className="flex flex-col gap-1">
            <span className="text-xs font-semibold uppercase tracking-wider text-muted">Key</span>
            <span className="tnum font-mono text-xs">{channel.api_key_masked || "Sealed"}</span>
          </div>
        </div>

        {/* Quick Diagnostics & Sync Actions */}
        <div className="flex flex-wrap items-center gap-2">
          <Button
            size="sm"
            variant="outline"
            disabled={runHealthCheck.pending}
            onClick={() => void runHealthCheck.mutate(undefined)}
          >
            {runHealthCheck.pending ? "Checking…" : "Run Health Check"}
          </Button>

          {!isCarrier ? (
            <>
              <Button
                size="sm"
                variant="outline"
                disabled={runSyncOrders.pending}
                onClick={() => void runSyncOrders.mutate(undefined)}
              >
                {runSyncOrders.pending ? "Syncing…" : "Sync Orders Now"}
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={runSyncInventory.pending}
                onClick={() => void runSyncInventory.mutate(undefined)}
              >
                {runSyncInventory.pending ? "Pushing…" : "Push Inventory Now"}
              </Button>
            </>
          ) : null}
        </div>

        {/* Settings form */}
        <div className="flex flex-col gap-4 border-t pt-4">
          <h4 className="text-sm font-bold uppercase tracking-wider text-muted">Configuration</h4>
          <TextField
            label="Connection name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            maxLength={120}
          />

          {warehouseOptions.length > 0 ? (
            <Select
              label="Assigned warehouse"
              value={warehouseId}
              onChange={(e) => setWarehouseId(e.target.value)}
              options={warehouseOptions}
            />
          ) : null}

          {!isCarrier ? (
            <Switch
              label="Auto-accept imported orders"
              description="Automatically confirms orders and reserves inventory when imported from this channel."
              checked={autoAccept}
              onChange={setAutoAccept}
            />
          ) : null}

          <div className="flex justify-end gap-2 pt-2">
            <Button
              variant="primary"
              disabled={saveConfig.pending}
              onClick={() => void saveConfig.mutate(undefined)}
            >
              {saveConfig.pending ? "Saving…" : "Save Changes"}
            </Button>
          </div>
        </div>

        {/* Rotate credentials */}
        <div className="flex flex-col gap-3 border-t pt-4">
          <div className="flex items-center justify-between">
            <h4 className="text-sm font-bold uppercase tracking-wider text-muted">Credentials</h4>
            <Button size="sm" variant="outline" onClick={() => setShowRotateKey(!showRotateKey)}>
              {showRotateKey ? "Cancel Rotation" : "Rotate API Key"}
            </Button>
          </div>

          {showRotateKey ? (
            <div className="flex flex-col gap-3 rounded-card bg-surface-muted p-4">
              <TextField
                label="New API Key / Access Token"
                type="password"
                value={newApiKey}
                onChange={(e) => setNewApiKey(e.target.value)}
                placeholder="Enter fresh credentials"
                required
              />
              {channel.provider === "shopify" ? (
                <TextField
                  label="New Webhook Secret (optional)"
                  type="password"
                  value={newWebhookSecret}
                  onChange={(e) => setNewWebhookSecret(e.target.value)}
                  placeholder="Leave empty to clear or enter updated secret"
                />
              ) : null}
              <div className="flex justify-end gap-2">
                <Button
                  size="sm"
                  variant="primary"
                  disabled={rotateKey.pending || !newApiKey.trim()}
                  onClick={() => void rotateKey.mutate(undefined)}
                >
                  {rotateKey.pending ? "Reconnecting…" : "Confirm Reconnect"}
                </Button>
              </div>
            </div>
          ) : null}
        </div>

        {/* Danger zone */}
        <div className="flex items-center justify-between border-t border-danger-line pt-4">
          <div>
            <p className="text-sm font-bold text-danger">Disconnect Integration</p>
            <p className="text-xs text-muted">Stops syncing orders, inventory and parcel tracking.</p>
          </div>
          <Button
            size="sm"
            variant="danger"
            disabled={disconnect.pending}
            onClick={() => void disconnect.mutate(undefined)}
          >
            {disconnect.pending ? "Disconnecting…" : "Disconnect"}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
