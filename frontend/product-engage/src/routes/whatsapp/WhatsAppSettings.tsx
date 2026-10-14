import { useCallback, useRef, useState } from "react";
import type { ReactNode } from "react";

import { useCan, useMutation } from "@opsgrids/client";
import {
  AlertTriangleIcon,
  CheckIcon,
  ConfirmDialog,
  MessageSquareIcon,
  Menu,
  PlusIcon,
  RefreshIcon,
  cn,
  formatRelative,
  initials,
} from "@opsgrids/ui";

import { RequireGrant } from "@/auth/RequireGrant";
import {
  disconnectWhatsApp,
  getWhatsAppHealth,
  listWhatsAppNumbers,
  setDefaultNumber,
  syncWhatsAppTemplates,
} from "@/api/whatsappSignup";
import type { WhatsAppHealth, WhatsAppNumber } from "@/api/whatsappSignup";
import { failureMessage } from "@/lib/errors";
import { formatInstant, formatNumber } from "@/lib/format";
import { usePolling } from "@/lib/usePolling";
import { useFocusHeading } from "@/lib/useFocusHeading";
import {
  Announcer,
  Button,
  Card,
  EmptyState,
  InlineAlert,
  PageHeading,
  Skeleton,
  StatusBadge,
  useAnnounce,
} from "@/ui";
import {
  displayNameLabel,
  IDENTITY_REVOKED_TEXT,
  limitLabel,
  maskedId,
  numberStatus,
  qualityLabel,
  qualityTone,
  TOKEN_TEXT,
  WEBHOOK_TEXT,
} from "./labels";
import { LinkButton, LoadError } from "./parts";
import { useWabaId } from "./useWabaId";

const WRITE = "engage.setup.whatsapp.write";
const READ = "engage.setup.whatsapp.read";

function When({ iso, children }: { iso: string; children: (relative: string) => ReactNode }) {
  return (
    <time dateTime={iso} title={formatInstant(iso)}>
      {children(formatRelative(iso))}
    </time>
  );
}

/* ------------------------------------ business account ------------------------------------ */

function BusinessAccount({
  connected,
  defaultNumber,
  wabaId,
  canWrite,
  onDisconnect,
}: {
  connected: boolean;
  defaultNumber: WhatsAppNumber | null;
  wabaId: string | null;
  canWrite: boolean;
  onDisconnect: () => void;
}) {
  const name = defaultNumber?.verified_name ?? "WhatsApp Business Account";
  return (
    <Card role="region" aria-label="Business account" className="flex flex-col gap-4 p-6">
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-card-title text-ink">Business Account</h2>
        <StatusBadge
          tone={connected ? "success" : "danger"}
          label={connected ? "Connected" : "Disconnected"}
        />
      </div>

      {connected ? (
        <div className="flex flex-wrap items-center gap-4">
          <span
            aria-hidden
            className="grid size-14 shrink-0 place-items-center rounded-full bg-sunken text-section text-ink-soft"
          >
            {initials(name)}
          </span>
          <div className="flex min-w-40 flex-1 flex-col gap-1">
            <span className="text-section text-ink">{name}</span>
            <span className="text-body-sm text-muted">
              WhatsApp Business Account{wabaId !== null ? `: ${maskedId(wabaId)}` : ""}
            </span>
          </div>
          {canWrite ? (
            <div className="flex flex-wrap gap-3">
              <LinkButton to="/whatsapp/connect">Reconnect</LinkButton>
              <Button
                variant="outline"
                onClick={onDisconnect}
                className="border-negative text-negative"
              >
                Disconnect
              </Button>
            </div>
          ) : null}
        </div>
      ) : (
        <EmptyState
          icon={<MessageSquareIcon className="size-6" />}
          title="WhatsApp isn't connected"
          description={
            canWrite
              ? "Connect your WhatsApp Business Account to send templates and answer customers. Your templates, automations and conversations are kept if you connected before."
              : "Someone who is an Owner or Admin in your organization can connect it."
          }
          action={
            canWrite ? (
              <LinkButton to="/whatsapp/connect" variant="primary">
                Connect WhatsApp
              </LinkButton>
            ) : undefined
          }
        />
      )}
    </Card>
  );
}

/* ------------------------------------- phone numbers ------------------------------------- */

function NumberRow({
  number,
  canWrite,
  onMakeDefault,
}: {
  number: WhatsAppNumber;
  canWrite: boolean;
  onMakeDefault: (number: WhatsAppNumber) => void;
}) {
  const status = numberStatus(number, "settings");
  const items = [
    {
      label: "Make default number",
      onSelect: () => onMakeDefault(number),
      hidden: !canWrite || number.is_default,
      disabled: number.status !== "connected",
      disabledReason: "Only a connected number can be the default.",
    },
  ];
  const hasActions = items.some((item) => item.hidden !== true);
  return (
    <tr className="border-t border-line align-middle">
      <th scope="row" className="p-4 text-left font-normal">
        <span className="flex flex-col gap-1">
          <span className="text-body font-bold text-ink tnum">{number.display_phone_number}</span>
          <span className="flex flex-wrap gap-2">
            <StatusBadge tone={status.tone} label={status.label} />
            {number.is_default ? <StatusBadge tone="info" label="Default" /> : null}
          </span>
        </span>
      </th>
      <td className="p-4 text-body-sm font-semibold text-ink">{displayNameLabel(number)}</td>
      <td className="p-4">
        <StatusBadge tone={qualityTone(number.quality_rating)} label={qualityLabel(number.quality_rating)} />
      </td>
      <td className="p-4 text-body-sm font-semibold text-ink">{limitLabel(number.messaging_limit)}</td>
      <td className="w-14 p-4 text-right">
        {hasActions ? (
          <Menu
            label={`More actions for ${number.display_phone_number}`}
            trigger="icon"
            align="end"
            items={items}
          />
        ) : null}
      </td>
    </tr>
  );
}

function PhoneNumbers({
  numbers,
  canWrite,
  onMakeDefault,
}: {
  numbers: WhatsAppNumber[];
  canWrite: boolean;
  onMakeDefault: (number: WhatsAppNumber) => void;
}) {
  return (
    <Card role="region" aria-label="Phone numbers">
      <div className="flex flex-wrap items-center justify-between gap-3 p-6 pb-4">
        <h2 className="text-card-title text-ink">Phone Numbers</h2>
        {canWrite ? (
          <LinkButton to="/whatsapp/connect" variant="primary">
            <PlusIcon aria-hidden className="size-4" />
            Add Phone Number
          </LinkButton>
        ) : null}
      </div>
      {numbers.length === 0 ? (
        <EmptyState
          title="No phone numbers yet"
          description="Numbers appear here once Meta has shared them with OpsGrids."
        />
      ) : (
        <div className="relative max-md:overflow-x-auto">
          <table className="w-full min-w-160 border-collapse md:min-w-0">
            <caption className="sr-only">Phone numbers on your WhatsApp Business Account</caption>
            <thead>
              <tr className="text-left text-caption font-semibold text-muted">
                <th scope="col" className="px-4 pb-2">Number</th>
                <th scope="col" className="px-4 pb-2">Display name</th>
                <th scope="col" className="px-4 pb-2">Quality</th>
                <th scope="col" className="px-4 pb-2">Messaging limit</th>
                <th scope="col" className="px-4 pb-2">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {numbers.map((number) => (
                <NumberRow key={number.id} number={number} canWrite={canWrite} onMakeDefault={onMakeDefault} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

/* ------------------------------------ connection health ----------------------------------- */

function HealthRow({
  title,
  ok,
  children,
  action,
}: {
  title: string;
  ok: boolean;
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <li className="flex flex-wrap items-center gap-4 border-t border-line p-4 px-6">
      <span
        aria-hidden
        className={cn(
          "grid size-7 shrink-0 place-items-center rounded-full",
          ok ? "bg-positive-soft text-positive" : "bg-late-soft text-late",
        )}
      >
        {ok ? <CheckIcon className="size-4" strokeWidth={2.6} /> : <AlertTriangleIcon className="size-4" />}
      </span>
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <span className="text-body font-bold text-ink">{title}</span>
        <span className="text-body-sm text-muted">{children}</span>
      </div>
      {action}
    </li>
  );
}

function ConnectionHealth({
  health,
  canWrite,
  onSynced,
}: {
  health: WhatsAppHealth;
  canWrite: boolean;
  onSynced: () => void;
}) {
  const announce = useAnnounce();
  const sync = useMutation(() => syncWhatsAppTemplates(), {
    onSuccess: (result) => {
      announce(`Templates synced. ${formatNumber(result.synced)} checked.`);
      onSynced();
    },
  });
  const { token, webhook, templates } = health;

  return (
    <Card role="region" aria-label="Connection health" className="overflow-hidden">
      <div className="p-6 pb-3">
        <h2 className="text-card-title text-ink">Connection health</h2>
      </div>
      <ul className="m-0 list-none p-0">
        <HealthRow title="Access token" ok={token.state === "valid"}>
          {TOKEN_TEXT[token.state]}
          {token.checked_at !== null ? (
            <>
              {" "}
              Checked <When iso={token.checked_at}>{(relative) => relative}</When>.
            </>
          ) : null}
          {token.identity_revoked_at !== null ? (
            <>
              {" "}
              {IDENTITY_REVOKED_TEXT}
            </>
          ) : null}
        </HealthRow>
        <HealthRow title="Webhook" ok={webhook.state === "receiving"}>
          {webhook.state === "receiving" ? (
            webhook.last_event_at !== null ? (
              <>
                Receiving events. Last event <When iso={webhook.last_event_at}>{(relative) => relative}</When>.
              </>
            ) : (
              "Receiving events."
            )
          ) : (
            WEBHOOK_TEXT[webhook.state]
          )}
        </HealthRow>
        <HealthRow
          title="Templates"
          ok
          action={
            canWrite ? (
              <Button
                variant="outline"
                size="sm"
                loading={sync.pending}
                onClick={() => void sync.mutate(undefined)}
                leadingIcon={<RefreshIcon aria-hidden className="size-4" />}
              >
                {sync.pending ? "Syncing…" : "Sync now"}
              </Button>
            ) : undefined
          }
        >
          {formatNumber(templates.count)} templates synced.
          {templates.synced_at !== null ? (
            <>
              {" "}
              Last sync <When iso={templates.synced_at}>{(relative) => relative}</When>.
            </>
          ) : (
            " Not synced yet."
          )}
        </HealthRow>
      </ul>
      {sync.error !== null ? (
        <div className="px-6 pb-4">
          <InlineAlert tone="danger" title="Templates weren't synced">
            {failureMessage(sync.error)}
          </InlineAlert>
        </div>
      ) : null}
      <p className="border-t border-line p-4 px-6 text-caption text-muted">
        If a check fails, choose Reconnect above. Your templates, automations and conversations are kept.
      </p>
    </Card>
  );
}

/* ---------------------------------------- the screen ---------------------------------------- */

function SettingsSkeleton() {
  return (
    <div className="flex flex-col gap-4" aria-hidden>
      <Skeleton className="h-36 w-full rounded-card" />
      <Skeleton className="h-64 w-full rounded-card" />
      <Skeleton className="h-52 w-full rounded-card" />
    </div>
  );
}

const NO_NUMBERS: WhatsAppNumber[] = [];

function WhatsAppSettings() {
  const announce = useAnnounce();
  const canWrite = useCan(WRITE, "ORG");
  const wabaId = useWabaId();
  const headingRef = useRef<HTMLHeadingElement>(null);
  const focusHeading = useFocusHeading(headingRef);

  const loader = useCallback(() => Promise.all([getWhatsAppHealth(), listWhatsAppNumbers()]), []);
  const state = usePolling(loader, [], null);

  const [confirming, setConfirming] = useState(false);
  const disconnect = useMutation(() => disconnectWhatsApp(), {
    onSuccess: () => {
      setConfirming(false);
      announce("WhatsApp disconnected. Your templates, automations and conversations are kept.");
      state.refresh();
      focusHeading();
    },
  });
  const makeDefault = useMutation((number: WhatsAppNumber) => setDefaultNumber(number.id), {
    onSuccess: (_result, number) => {
      announce(`${number.display_phone_number} is now the default number.`);
      state.refresh();
    },
  });

  const health = state.status === "success" ? state.data[0] : null;
  const numbers = state.status === "success" ? state.data[1].items : NO_NUMBERS;
  const defaultNumber = numbers.find((n) => n.is_default) ?? numbers[0] ?? null;

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeading
        ref={headingRef}
        eyebrow="WhatsApp"
        title="WhatsApp Settings"
        description="Manage your WhatsApp Business Account, phone numbers and connection."
      />

      {state.status === "loading" ? <SettingsSkeleton /> : null}
      {state.status === "error" ? (
        <LoadError error={state.error} title="Couldn't load your WhatsApp settings" onRetry={state.refresh} />
      ) : null}

      {state.status === "success" && health !== null ? (
        <>
          {state.stopped ? (
            <InlineAlert tone="warning" title="This page can't refresh">
              Your access changed, so what is shown may be out of date. Reload the page.
            </InlineAlert>
          ) : null}
          <div aria-live="polite" className="empty:hidden">
            {makeDefault.error !== null ? (
              <InlineAlert tone="danger" title="The default number wasn't changed">
                {failureMessage(makeDefault.error)}
              </InlineAlert>
            ) : null}
          </div>
          <BusinessAccount
            connected={health.connected}
            defaultNumber={defaultNumber}
            wabaId={wabaId}
            canWrite={canWrite}
            onDisconnect={() => {
              disconnect.reset();
              setConfirming(true);
            }}
          />
          {health.connected || numbers.length > 0 ? (
            <PhoneNumbers
              numbers={numbers}
              canWrite={canWrite}
              onMakeDefault={(number) => void makeDefault.mutate(number)}
            />
          ) : null}
          {health.connected ? (
            <ConnectionHealth health={health} canWrite={canWrite} onSynced={state.refresh} />
          ) : null}
        </>
      ) : null}

      <ConfirmDialog
        open={confirming}
        onClose={() => setConfirming(false)}
        onConfirm={() => void disconnect.mutate(undefined)}
        title="Disconnect WhatsApp?"
        body="OpsGrids will delete the saved access token and stop sending and receiving WhatsApp messages. Your templates, automations and conversation history are kept, and you can reconnect at any time."
        confirmLabel="Disconnect"
        pendingLabel="Disconnecting…"
        pending={disconnect.pending}
        {...(disconnect.error !== null ? { error: failureMessage(disconnect.error) } : {})}
      />
    </div>
  );
}

export function WhatsAppSettingsPage() {
  return (
    <Announcer>
      <RequireGrant permission={READ} what="WhatsApp settings">
        <WhatsAppSettings />
      </RequireGrant>
    </Announcer>
  );
}
