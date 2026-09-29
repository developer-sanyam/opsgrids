import { useState } from "react";

import {
  checkChannelHealth,
  deleteSkuMapping,
  listSkuMappings,
  listSyncLogs,
  retryChannelSyncLog,
  syncChannelInventoryNow,
  syncChannelOrdersNow,
  upsertSkuMapping,
} from "@/api/client";
import type {
  ChannelHealthCheckOut,
  ChannelOut,
  ChannelSkuMappingOut,
  ChannelSyncLogOut,
  ChannelSyncResultOut,
  InventoryItemOut,
} from "@/api/types";
import { Can, problemMessage, useAsync, useCan, useMutation } from "@opsgrids/client";
import type { Column, ComboboxOption } from "@/ui";
import {
  Button,
  ChannelSyncStatusPill,
  DataTable,
  Drawer,
  EmptyState,
  ErrorState,
  InlineAlert,
  Pill,
  RowActions,
  Skeleton,
} from "@/ui";
import { SkuPicker } from "./SkuPicker";
import { formatDateTime } from "@/lib/format";

interface ChannelDetailDrawerProps {
  channel: ChannelOut;
  onClose: () => void;
  onChanged: () => void;
}

function formatWhen(value: string | null): string {
  return value ? formatDateTime(value) : "Never";
}

/**
 * SKU mapping and sync history for one channel (Fulfilment F11) — the admin UI's own view onto
 * `channel_sku_mappings` and `channel_sync_logs`, real data throughout: a mapping is added
 * through `POST .../sku-mappings`, a sync is triggered through the real connector
 * (`app.channels.services`), and a retry re-attempts the exact action that failed, never a
 * decorative "retried!" toast with nothing behind it.
 */
export function ChannelDetailDrawer({ channel, onClose, onChanged }: ChannelDetailDrawerProps) {
  const canSync = useCan("operations.channels.channel.sync", "ORG");
  const canWrite = useCan("operations.channels.channel.write", "ORG");
  const [logStatus, setLogStatus] = useState<"all" | "success" | "failed">("all");
  const [mappingSku, setMappingSku] = useState<ComboboxOption | null>(null);
  const [mappingItem, setMappingItem] = useState<InventoryItemOut | null>(null);
  const [mappingError, setMappingError] = useState<string | undefined>(undefined);

  const mappingsState = useAsync(() => listSkuMappings(channel.id), [channel.id]);
  const logsState = useAsync(
    () => listSyncLogs(channel.id, { status: logStatus, size: 50 }),
    [channel.id, logStatus],
  );

  const [orderResult, setOrderResult] = useState<ChannelSyncResultOut | null>(null);
  const [inventoryResult, setInventoryResult] = useState<ChannelSyncResultOut | null>(null);
  const [healthResult, setHealthResult] = useState<ChannelHealthCheckOut | null>(null);

  const syncOrders = useMutation<void, ChannelSyncResultOut>(() => syncChannelOrdersNow(channel.id), {
    onSuccess: (result) => {
      setOrderResult(result);
      logsState.reload();
      onChanged();
    },
  });
  const syncInventory = useMutation<void, ChannelSyncResultOut>(() => syncChannelInventoryNow(channel.id), {
    onSuccess: (result) => {
      setInventoryResult(result);
      mappingsState.reload();
      logsState.reload();
      onChanged();
    },
  });
  const healthCheck = useMutation<void, ChannelHealthCheckOut>(() => checkChannelHealth(channel.id), {
    onSuccess: (result) => {
      setHealthResult(result);
      logsState.reload();
      onChanged();
    },
  });
  const addMapping = useMutation(
    (input: { external_sku: string; inventory_item_id: string }) => upsertSkuMapping(channel.id, input),
    {
      onSuccess: () => {
        setMappingSku(null);
        setMappingItem(null);
        mappingsState.reload();
      },
    },
  );
  const removeMapping = useMutation((mapping: ChannelSkuMappingOut) => deleteSkuMapping(channel.id, mapping.id), {
    onSuccess: () => mappingsState.reload(),
  });
  const retryLog = useMutation((log: ChannelSyncLogOut) => retryChannelSyncLog(channel.id, log.id), {
    onSuccess: () => {
      logsState.reload();
      mappingsState.reload();
      onChanged();
    },
  });

  function submitMapping() {
    setMappingError(undefined);
    if (!mappingSku || !mappingItem) {
      setMappingError("Look up an inventory item to map this SKU to.");
      return;
    }
    void addMapping.mutate({ external_sku: mappingSku.id, inventory_item_id: mappingItem.id });
  }

  const mappingColumns: Column<ChannelSkuMappingOut>[] = [
    {
      key: "sku",
      header: "External SKU",
      render: (m) => <span className="font-semibold text-ink">{m.external_sku}</span>,
    },
    {
      key: "push",
      header: "Last push",
      render: (m) => (
        <div className="flex flex-col leading-tight">
          <span className="text-xs tnum text-muted">{formatWhen(m.last_pushed_at)}</span>
          {m.last_push_status === "failed" ? (
            <span className="text-xs text-danger">{m.last_push_error ?? "Failed"}</span>
          ) : null}
        </div>
      ),
    },
    {
      key: "actions",
      header: "",
      align: "right",
      render: (m) => (
        <RowActions
          label={`Mapping for ${m.external_sku}`}
          actions={[
            {
              label: "Remove mapping",
              tone: "danger",
              onSelect: () => void removeMapping.mutate(m),
              hidden: !canWrite,
            },
          ]}
        />
      ),
    },
  ];

  const logColumns: Column<ChannelSyncLogOut>[] = [
    {
      key: "type",
      header: "Type",
      render: (l) => (
        <div className="flex flex-col leading-tight">
          <span className="font-semibold text-ink capitalize">{l.sync_type.replace("_", " ")}</span>
          <span className="text-xs text-faint">{l.external_ref || "—"}</span>
        </div>
      ),
    },
    {
      key: "status",
      header: "Status",
      render: (l) => (
        <div className="flex flex-col gap-1">
          <ChannelSyncStatusPill status={l.status} />
          {l.status === "failed" && l.error_message ? (
            <span className="text-xs text-danger">{l.error_message}</span>
          ) : null}
        </div>
      ),
    },
    {
      key: "when",
      header: "When",
      render: (l) => <span className="text-xs tnum text-muted">{formatDateTime(l.attempted_at)}</span>,
    },
    {
      key: "actions",
      header: "",
      align: "right",
      render: (l) => (
        <RowActions
          label={`Sync attempt ${l.sync_type}`}
          actions={[
            {
              label: "Retry",
              onSelect: () => void retryLog.mutate(l),
              hidden: !canSync || l.status !== "failed",
            },
          ]}
        />
      ),
    },
  ];

  return (
    <Drawer open onClose={onClose} title={channel.name} description={channel.external_shop_id} size="lg">
      <div className="flex flex-col gap-6">
        <Can permission="operations.channels.channel.sync" scope="ORG">
          <div className="flex flex-wrap gap-2">
            <Button size="sm" variant="outline" onClick={() => void syncOrders.mutate(undefined)} disabled={syncOrders.pending}>
              {syncOrders.pending ? "Syncing…" : "Sync orders now"}
            </Button>
            <Button size="sm" variant="outline" onClick={() => void syncInventory.mutate(undefined)} disabled={syncInventory.pending}>
              {syncInventory.pending ? "Syncing…" : "Sync inventory now"}
            </Button>
            <Button size="sm" variant="outline" onClick={() => void healthCheck.mutate(undefined)} disabled={healthCheck.pending}>
              {healthCheck.pending ? "Checking…" : "Check connection"}
            </Button>
          </div>
          {orderResult ? (
            <InlineAlert
              tone={orderResult.rejected > 0 ? "warning" : "success"}
              title={`Orders: ${orderResult.succeeded} imported, ${orderResult.rejected} rejected, ${orderResult.attempted} attempted`}
            />
          ) : null}
          {inventoryResult ? (
            <InlineAlert tone="success" title={`Inventory: ${inventoryResult.succeeded} of ${inventoryResult.attempted} SKUs pushed`} />
          ) : null}
          {healthResult ? (
            <InlineAlert
              tone={healthResult.healthy ? "success" : "danger"}
              title={healthResult.healthy ? "Connection is healthy" : "Connection reported unhealthy"}
            />
          ) : null}
          {syncOrders.error ? <InlineAlert tone="danger" title={problemMessage(syncOrders.error)} /> : null}
          {syncInventory.error ? <InlineAlert tone="danger" title={problemMessage(syncInventory.error)} /> : null}
        </Can>

        <section className="flex flex-col gap-3">
          <h3 className="text-sm font-semibold text-ink">SKU mappings</h3>
          <Can permission="operations.channels.channel.write" scope="ORG">
            <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
              <div className="flex-1">
                <SkuPicker
                  value={mappingSku}
                  onChange={setMappingSku}
                  onPick={setMappingItem}
                  {...(mappingError !== undefined ? { error: mappingError } : {})}
                />
              </div>
              <Button size="sm" onClick={submitMapping} disabled={addMapping.pending}>
                {addMapping.pending ? "Mapping…" : "Map SKU"}
              </Button>
            </div>
            {addMapping.error ? <InlineAlert tone="danger" title={problemMessage(addMapping.error)} /> : null}
          </Can>
          {mappingsState.status === "loading" ? <Skeleton className="h-24 w-full rounded-tile" /> : null}
          {mappingsState.status === "error" ? (
            <ErrorState title="Couldn't load SKU mappings" message={problemMessage(mappingsState.error)} onRetry={mappingsState.reload} />
          ) : null}
          {mappingsState.status === "success" ? (
            <DataTable
              columns={mappingColumns}
              rows={mappingsState.data.items}
              rowKey={(m) => m.id}
              caption="SKU mappings"
              emptyState={<EmptyState title="No SKU mappings yet" description="Map an external SKU to an inventory item so an order naming it can import." />}
            />
          ) : null}
        </section>

        <section className="flex flex-col gap-3">
          <h3 className="text-sm font-semibold text-ink">Sync log</h3>
          <div className="flex items-center gap-2">
            {(["all", "success", "failed"] as const).map((s) => (
              <Pill key={s} active={logStatus === s} onClick={() => setLogStatus(s)}>
                {s === "all" ? "All" : s === "success" ? "Success" : "Failed"}
              </Pill>
            ))}
          </div>
          {retryLog.error ? <InlineAlert tone="danger" title={problemMessage(retryLog.error)} /> : null}
          {logsState.status === "loading" ? <Skeleton className="h-24 w-full rounded-tile" /> : null}
          {logsState.status === "error" ? (
            <ErrorState title="Couldn't load the sync log" message={problemMessage(logsState.error)} onRetry={logsState.reload} />
          ) : null}
          {logsState.status === "success" ? (
            <DataTable
              columns={logColumns}
              rows={logsState.data.items}
              rowKey={(l) => l.id}
              caption="Sync log"
              emptyState={<EmptyState title="No sync attempts yet" description="Runs on its own once this connection has SKU mappings, or trigger one above." />}
            />
          ) : null}
        </section>
      </div>
    </Drawer>
  );
}
