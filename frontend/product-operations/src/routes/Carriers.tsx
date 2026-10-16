import { useState } from "react";

import { disconnectChannel, getWebhookSetup, listChannels, reconnectChannel } from "@/api/client";
import type { ChannelOut } from "@/api/types";
import { Can, problemMessage, useAsync, useCan, useMutation } from "@opsgrids/client";
import { ConnectCarrierDialog } from "@/forms/ConnectCarrierDialog";
import { formatDateTime } from "@/lib/format";
import {
  Button,
  Card,
  ChannelStatusPill,
  ConfirmDialog,
  CopyField,
  DataTable,
  EmptyState,
  ErrorState,
  InlineAlert,
  PageHeader,
  Pagination,
  RowActions,
  Skeleton,
  TextField,
} from "@/ui";
import type { Column } from "@/ui";
import { TruckIcon } from "@opsgrids/ui";

/**
 * Couriers this business has connected with its **own** account (`channels.kind = "carrier"`).
 * Dispatch offers these in its carrier picker; once connected, tracking updates reach the
 * shipment through the webhook set up here. Credentials are sealed server-side and never shown;
 * the webhook token is the one secret a person copies out, and every view of it is audited.
 */
export function CarriersPage() {
  const canWrite = useCan("operations.channels.channel.write", "ORG");
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(25);
  const [connecting, setConnecting] = useState(false);
  const [webhookFor, setWebhookFor] = useState<ChannelOut | null>(null);
  const [disconnecting, setDisconnecting] = useState<ChannelOut | null>(null);
  const [reconnecting, setReconnecting] = useState<ChannelOut | null>(null);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [credentialError, setCredentialError] = useState<string | undefined>(undefined);

  const state = useAsync(() => listChannels({ kind: "carrier", page, size }), [page, size]);

  const disconnect = useMutation((c: ChannelOut) => disconnectChannel(c.id), {
    onSuccess: () => {
      setDisconnecting(null);
      state.reload();
    },
  });
  const reconnect = useMutation(
    (input: { channel: ChannelOut; api_key: string; api_secret?: string }) =>
      reconnectChannel(input.channel.id, {
        api_key: input.api_key,
        ...(input.api_secret !== undefined ? { api_secret: input.api_secret } : {}),
      }),
    {
      onSuccess: () => {
        setReconnecting(null);
        setEmail("");
        setPassword("");
        state.reload();
      },
    },
  );

  function submitReconnect() {
    setCredentialError(undefined);
    const needsPassword = reconnecting?.provider === "shiprocket";
    if (email.trim().length < 1 || (needsPassword && password.length < 1)) {
      setCredentialError(
        needsPassword ? "Both the API user's email and password are needed to reconnect." : "The API token is needed to reconnect.",
      );
      return;
    }
    if (reconnecting) {
      void reconnect.mutate({ channel: reconnecting, api_key: email.trim(), ...(needsPassword ? { api_secret: password } : {}) });
    }
  }

  const columns: Column<ChannelOut>[] = [
    {
      key: "name",
      header: "Courier",
      mobile: "title",
      render: (c) => (
        <span className="font-semibold text-ink">
          {c.name}
          <span className="block text-xs font-normal text-faint">Pickup: {c.external_shop_id}</span>
        </span>
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
      key: "connected",
      header: "Connected",
      cellClassName: "hidden sm:table-cell",
      headerClassName: "hidden sm:table-cell",
      render: (c) => <span className="tnum text-muted">{formatDateTime(c.connected_at)}</span>,
    },
    {
      key: "actions",
      header: "",
      mobile: "actions",
      align: "right",
      render: (c) => (
        <RowActions
          label={`Courier ${c.name}`}
          actions={[
            {
              label: "Tracking webhook",
              onSelect: () => setWebhookFor(c),
              hidden: !canWrite || c.status === "disconnected",
            },
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

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Fulfilment"
        title="Carriers"
        description="Couriers you ship with, connected with your own account. Pick one when you hand an order over, and tracking updates the shipment."
        actions={
          <Can permission="operations.channels.channel.write" scope="ORG">
            <Button onClick={() => setConnecting(true)}>Connect courier</Button>
          </Can>
        }
      />

      {disconnect.error ? <InlineAlert tone="danger" title={problemMessage(disconnect.error)} /> : null}

      <Card className="p-2 sm:p-4">
        {state.status === "loading" ? (
          <div className="flex flex-col gap-3 p-3">
            {Array.from({ length: 3 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full rounded-tile" />
            ))}
          </div>
        ) : null}
        {state.status === "error" ? (
          <ErrorState title="Couldn't load couriers" message={problemMessage(state.error)} onRetry={state.reload} />
        ) : null}
        {state.status === "success" ? (
          <>
            <DataTable
              columns={columns}
              rows={state.data.items}
              rowKey={(c) => c.id}
              caption="Carriers"
              emptyState={
                <EmptyState
                  icon={<TruckIcon className="size-6" />}
                  title="No couriers connected yet"
                  description="Connect your Shiprocket or Delhivery account to book shipments and get tracking without leaving dispatch."
                />
              }
            />
            <Pagination
              page={state.data.page}
              pages={state.data.pages}
              total={state.data.total}
              size={state.data.size}
              noun="couriers"
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
          <ConnectCarrierDialog
            onClose={() => setConnecting(false)}
            onConnected={() => {
              setConnecting(false);
              state.reload();
            }}
          />
        </Can>
      ) : null}

      {webhookFor ? <WebhookSetup channel={webhookFor} onClose={() => setWebhookFor(null)} /> : null}

      <ConfirmDialog
        open={disconnecting !== null}
        onClose={() => {
          setDisconnecting(null);
          disconnect.reset();
        }}
        onConfirm={() => {
          if (disconnecting) void disconnect.mutate(disconnecting);
        }}
        title="Disconnect this courier?"
        body="No new bookings can be made with it and tracking stops updating shipments until it is reconnected. Shipments already booked are unaffected."
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
          setEmail("");
          setPassword("");
          setCredentialError(undefined);
          reconnect.reset();
        }}
        onConfirm={submitReconnect}
        title={`Reconnect ${reconnecting?.name ?? "this courier"}?`}
        body="Replaces the stored login and clears any error. The tracking webhook you registered keeps working."
        confirmLabel="Reconnect"
        pendingLabel="Reconnecting…"
        pending={reconnect.pending}
        {...(reconnect.error ? { error: problemMessage(reconnect.error) } : {})}
      >
        {reconnecting?.provider === "delhivery" ? (
          <TextField
            label="API token"
            type="password"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            maxLength={500}
            autoComplete="off"
            required
            {...(credentialError !== undefined ? { error: credentialError } : {})}
          />
        ) : (
          <>
            <TextField
              label="API user email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              maxLength={500}
              autoComplete="off"
              required
            />
            <TextField
              label="API user password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              maxLength={200}
              autoComplete="new-password"
              required
              {...(credentialError !== undefined ? { error: credentialError } : {})}
            />
          </>
        )}
      </ConfirmDialog>
    </div>
  );
}

/** The two values to register in the courier's panel. Fetched on open: each view is audited. */
function WebhookSetup({ channel, onClose }: { channel: ChannelOut; onClose: () => void }) {
  const setup = useAsync(() => getWebhookSetup(channel.id), [channel.id]);
  return (
    <ConfirmDialog
      open
      onClose={onClose}
      onConfirm={onClose}
      title="Tracking webhook"
      body={
        channel.provider === "delhivery"
          ? "Delhivery switches scan pushes on for you: email lastmile-integration@delhivery.com the Webhook Requirement Document with this URL and the token as the Authorization header value. Tracking then updates your shipments as it happens."
          : "In Shiprocket, open Settings → API → Webhooks, add this URL, switch it on and paste the token as the security token. Tracking then updates your shipments as it happens."
      }
      confirmLabel="Done"
      tone="primary"
    >
      {setup.status === "loading" ? <Skeleton className="h-12 w-full rounded-tile" /> : null}
      {setup.status === "error" ? <InlineAlert tone="danger" title={problemMessage(setup.error)} /> : null}
      {setup.status === "success" ? (
        <div className="flex flex-col gap-3">
          <CopyField label="Webhook URL" value={`${window.location.origin}${setup.data.path}`} />
          <CopyField label="Security token" value={setup.data.token} />
        </div>
      ) : null}
    </ConfirmDialog>
  );
}
