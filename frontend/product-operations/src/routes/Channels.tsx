import { useState } from "react";

import { disconnectChannel, listChannels, listWarehouses, reconnectChannel } from "@/api/client";
import type { ChannelOut, ChannelStatus, WarehouseOut } from "@/api/types";
import { Can, problemMessage, useAsync, useCan, useMutation } from "@opsgrids/client";
import { ConnectChannelDialog } from "@/forms/ConnectChannelDialog";
import { ChannelDetailDrawer } from "@/forms/ChannelDetailDrawer";
import { formatDateTime } from "@/lib/format";
import {
  Button,
  Card,
  ChannelStatusPill,
  ConfirmDialog,
  DataTable,
  EmptyState,
  ErrorState,
  InlineAlert,
  PageHeader,
  Pagination,
  Pill,
  RowActions,
  Skeleton,
  TextField,
} from "@/ui";
import type { Column } from "@/ui";
import { PlugIcon } from "@opsgrids/ui";

type StatusFilter = ChannelStatus | "all";

const STATUS_FILTERS: { value: StatusFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "connected", label: "Connected" },
  { value: "disconnected", label: "Disconnected" },
  { value: "error", label: "Needs reconnect" },
];

/**
 * Connected external sales channels (Fulfilment plan F11) — a marketplace/storefront platform
 * this business also sells on. Real backend throughout: connecting seals a credential
 * (`app.channels.services.connect_channel`), and every sync action here drives the real
 * connector against this build's self-hosted sandbox marketplace, never a fixture. SKU mapping
 * and sync history live in the detail drawer (`ChannelDetailDrawer`).
 */
export function ChannelsPage() {
  const canWrite = useCan("operations.channels.channel.write", "ORG");
  const [status, setStatus] = useState<StatusFilter>("all");
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(25);
  const [connecting, setConnecting] = useState(false);
  const [detail, setDetail] = useState<ChannelOut | null>(null);
  const [disconnecting, setDisconnecting] = useState<ChannelOut | null>(null);
  const [reconnecting, setReconnecting] = useState<ChannelOut | null>(null);
  const [reconnectKey, setReconnectKey] = useState("");
  const [reconnectKeyError, setReconnectKeyError] = useState<string | undefined>(undefined);

  const loader = () => listChannels({ status, page, size });
  const state = useAsync(loader, [status, page, size]);
  const warehousesState = useAsync(() => listWarehouses({ size: 200 }), []);
  const warehouseName = (id: string | null): string => {
    if (!id || warehousesState.status !== "success") return "—";
    const w = warehousesState.data.items.find((row: WarehouseOut) => row.id === id);
    return w ? w.code : "—";
  };

  const disconnect = useMutation((c: ChannelOut) => disconnectChannel(c.id), {
    onSuccess: () => {
      setDisconnecting(null);
      state.reload();
    },
  });
  const reconnect = useMutation(
    (input: { channel: ChannelOut; api_key: string }) => reconnectChannel(input.channel.id, { api_key: input.api_key }),
    {
      onSuccess: () => {
        setReconnecting(null);
        setReconnectKey("");
        state.reload();
      },
    },
  );

  function submitReconnect() {
    setReconnectKeyError(undefined);
    if (reconnectKey.trim().length < 1) {
      setReconnectKeyError("A fresh API key is needed to reconnect.");
      return;
    }
    if (!reconnecting) return;
    void reconnect.mutate({ channel: reconnecting, api_key: reconnectKey.trim() });
  }

  const columns: Column<ChannelOut>[] = [
    {
      key: "name",
      header: "Channel",
      mobile: "title",
      render: (c) => (
        <button
          type="button"
          onClick={() => setDetail(c)}
          className="text-left font-semibold text-ink hover:text-accent transition-colors"
        >
          {c.name}
          <span className="block text-xs font-normal text-faint">{c.external_shop_id}</span>
        </button>
      ),
    },
    {
      key: "status",
      header: "Status",
      mobile: "status",
      render: (c) => (
        <div className="flex flex-col gap-1">
          <ChannelStatusPill status={c.status} />
          {c.status === "error" && c.last_error_message ? (
            <span className="text-xs text-danger">{c.last_error_message}</span>
          ) : null}
        </div>
      ),
    },
    {
      key: "warehouse",
      header: "Warehouse",
      cellClassName: "hidden sm:table-cell",
      headerClassName: "hidden sm:table-cell",
      render: (c) => <span className="tnum text-muted">{warehouseName(c.default_warehouse_id)}</span>,
    },
    {
      key: "auto_accept",
      header: "Auto-accept",
      mobile: "hidden",
      cellClassName: "hidden md:table-cell",
      headerClassName: "hidden md:table-cell",
      render: (c) => <span className="text-muted">{c.auto_accept_orders ? "On" : "Off"}</span>,
    },
    {
      key: "last_sync",
      header: "Last order sync",
      cellClassName: "hidden lg:table-cell",
      headerClassName: "hidden lg:table-cell",
      render: (c) => <span className="tnum text-muted">{formatDateTime(c.last_orders_synced_at)}</span>,
    },
    {
      key: "actions",
      header: "",
      mobile: "actions",
      align: "right",
      render: (c) => (
        <RowActions
          label={`Channel ${c.name}`}
          actions={[
            { label: "View details", onSelect: () => setDetail(c) },
            {
              label: "Reconnect",
              onSelect: () => setReconnecting(c),
              hidden: !canWrite || c.status === "connected",
            },
            {
              label: "Disconnect",
              tone: "danger",
              onSelect: () => setDisconnecting(c),
              hidden: !canWrite || c.status === "disconnected",
            },
          ]}
        />
      ),
    },
  ];

  const filtersActive = status !== "all";

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Fulfilment"
        title="Channels"
        description="External marketplaces and storefronts this business also sells on — connect one, map its SKUs, and orders import automatically."
        actions={
          <Can permission="operations.channels.channel.write" scope="ORG">
            <Button onClick={() => setConnecting(true)}>Connect channel</Button>
          </Can>
        }
      />

      <Card className="p-4 sm:p-6 flex flex-col gap-4">
        <div className="flex items-center gap-2 overflow-x-auto -mx-1 px-1">
          {STATUS_FILTERS.map((f) => (
            <Pill
              key={f.value}
              active={status === f.value}
              onClick={() => {
                setStatus(f.value);
                setPage(1);
              }}
            >
              {f.label}
            </Pill>
          ))}
        </div>
      </Card>

      {disconnect.error ? <InlineAlert tone="danger" title={problemMessage(disconnect.error)} /> : null}

      <Card className="p-2 sm:p-4">
        {state.status === "loading" ? (
          <div className="flex flex-col gap-3 p-3">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full rounded-tile" />
            ))}
          </div>
        ) : null}
        {state.status === "error" ? (
          <ErrorState title="Couldn't load channels" message={problemMessage(state.error)} onRetry={state.reload} />
        ) : null}
        {state.status === "success" ? (
          <>
            <DataTable
              columns={columns}
              rows={state.data.items}
              rowKey={(c) => c.id}
              caption="Channels"
              emptyState={
                <EmptyState
                  icon={<PlugIcon className="size-6" />}
                  title={filtersActive ? "No channels match this filter" : "No channels connected yet"}
                  description={
                    filtersActive
                      ? "Try a different status, or clear the filter."
                      : "Connect a channel to start importing its orders and pushing stock levels to it."
                  }
                />
              }
            />
            <Pagination
              page={state.data.page}
              pages={state.data.pages}
              total={state.data.total}
              size={state.data.size}
              noun="channels"
              onPageChange={setPage}
              onSizeChange={(n) => {
                setSize(n);
                setPage(1);
              }}
            />
          </>
        ) : null}
      </Card>

      {connecting ? (
        <Can permission="operations.channels.channel.write" scope="ORG">
          <ConnectChannelDialog
            onClose={() => setConnecting(false)}
            onConnected={() => {
              setConnecting(false);
              state.reload();
            }}
          />
        </Can>
      ) : null}

      {detail ? (
        <ChannelDetailDrawer
          channel={detail}
          onClose={() => setDetail(null)}
          onChanged={() => state.reload()}
        />
      ) : null}

      <ConfirmDialog
        open={disconnecting !== null}
        onClose={() => {
          setDisconnecting(null);
          disconnect.reset();
        }}
        onConfirm={() => {
          if (disconnecting) void disconnect.mutate(disconnecting);
        }}
        title="Disconnect this channel?"
        body="Stops all order import and inventory/tracking sync until it is reconnected. Existing orders it already imported are unaffected."
        confirmLabel="Disconnect"
        pendingLabel="Disconnecting…"
        tone="danger"
        pending={disconnect.pending}
        {...(disconnect.error ? { error: problemMessage(disconnect.error) } : {})}
      />

      <ConfirmDialog
        open={reconnecting !== null}
        onClose={() => {
          setReconnecting(null);
          setReconnectKey("");
          setReconnectKeyError(undefined);
          reconnect.reset();
        }}
        onConfirm={submitReconnect}
        title={`Reconnect ${reconnecting?.name ?? "this channel"}?`}
        body="Rotates the stored credential and clears any error status — nothing else about the connection changes."
        confirmLabel="Reconnect"
        pendingLabel="Reconnecting…"
        pending={reconnect.pending}
        {...(reconnect.error ? { error: problemMessage(reconnect.error) } : {})}
      >
        <TextField
          label="New API key"
          type="password"
          value={reconnectKey}
          onChange={(e) => setReconnectKey(e.target.value)}
          maxLength={500}
          required
          {...(reconnectKeyError !== undefined ? { error: reconnectKeyError } : {})}
        />
      </ConfirmDialog>
    </div>
  );
}
