import { useState } from "react";
import type { ReactNode } from "react";
import { useNavigate } from "react-router-dom";

import { Can, useAsync } from "@opsgrids/client";
import { MailIcon, Tabs } from "@opsgrids/ui";
import type { TabItem } from "@opsgrids/ui";

import { getConnection } from "@/api/client";
import type { EmailAccount } from "@/api/email";
import { connectionKindLabel, formatNumber } from "@/lib/format";
import { Button, Card, ConnectionStatusTag, EmptyState, LoadFailure, PageHeading, Skeleton } from "@/ui";

import { SendingAuthCard } from "./SendingAuthCard";

/**
 * The email account and how it authenticates.
 *
 * An organization has one email connection (`GET /engage/connection` is a page of at most one), so
 * the accounts list has at most one row; the design shows three because it was drawn for a product
 * that allows several. The tabs are the ones the connection has data for: its configuration and its
 * daily limit. Sent-today, bounce rate, tracking and webhook settings are not part of the
 * connection's payload, so those tabs are not drawn.
 *
 * The inbox-sync (IMAP) row appears only when the connection payload carries `imap_enabled`, and is
 * read-only: this app has no call that changes it.
 */

const WRITE = "engage.setup.connection.write";

const TABS: TabItem[] = [
  { id: "configuration", label: "Configuration" },
  { id: "limits", label: "Sending Limits" },
];

function ConnectButton() {
  const navigate = useNavigate();
  return (
    <Can permission={WRITE} scope="ORG">
      <Button onClick={() => navigate("/connection")}>Connect Account</Button>
    </Can>
  );
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-1">
      <dt className="text-xs font-bold uppercase tracking-wider text-faint">{label}</dt>
      <dd className="text-sm text-ink">{children}</dd>
    </div>
  );
}

function Configuration({ account }: { account: EmailAccount }) {
  return (
    <dl className="grid gap-4 pt-4 sm:grid-cols-2">
      <Fact label="Provider">{connectionKindLabel(account.kind)}</Fact>
      <Fact label="Username">{account.username}</Fact>
      <Fact label="Host">{account.host}</Fact>
      <Fact label="Port">{account.port}</Fact>
      {account.imap_enabled !== undefined ? (
        <Fact label="Inbox sync (IMAP)">{account.imap_enabled ? "On" : "Off"}</Fact>
      ) : null}
    </dl>
  );
}

function Limits({ account }: { account: EmailAccount }) {
  return (
    <div className="flex flex-col gap-3 pt-4">
      <dl className="grid gap-4 sm:grid-cols-2">
        <Fact label="Daily limit">{formatNumber(account.daily_cap)} emails</Fact>
      </dl>
      <p className="text-sm text-muted">
        This is what your provider accepts in a day. A campaign larger than it is refused before it starts.
      </p>
    </div>
  );
}

function AccountDetails({ account }: { account: EmailAccount }) {
  const navigate = useNavigate();
  const [tab, setTab] = useState("configuration");
  return (
    <Card className="flex flex-col gap-6 p-4 sm:p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex flex-col gap-1">
          <h2 className="text-lg font-bold text-ink">Account Details</h2>
          <p className="text-sm text-ink">{account.username}</p>
          <p className="text-sm text-muted">{connectionKindLabel(account.kind)}</p>
        </div>
        <div className="flex items-center gap-2">
          <ConnectionStatusTag status={account.status} />
          <Can permission={WRITE} scope="ORG">
            <Button variant="secondary" size="sm" onClick={() => navigate("/connection")}>
              Edit
            </Button>
          </Can>
        </div>
      </div>

      <Tabs label="Account settings" items={TABS} value={tab} onChange={setTab}>
        {tab === "configuration" ? <Configuration account={account} /> : <Limits account={account} />}
      </Tabs>

      <SendingAuthCard />
    </Card>
  );
}

function AccountList({ account }: { account: EmailAccount }) {
  return (
    <Card className="flex flex-col gap-4 p-4 sm:p-6">
      <h2 className="text-base font-bold text-ink">All Accounts (1)</h2>
      <ul>
        <li className="flex items-center gap-3 rounded-tile bg-ground p-3">
          <span className="flex min-w-0 flex-1 flex-col">
            <span className="truncate text-sm font-bold text-ink">{account.username}</span>
            <span className="text-xs text-muted">{connectionKindLabel(account.kind)}</span>
          </span>
          <ConnectionStatusTag status={account.status} />
        </li>
      </ul>
      <p className="text-xs text-muted">
        Credentials are encrypted and only the communication service can read them. Each account belongs
        to one organization.
      </p>
    </Card>
  );
}

export function EmailSettingsPage() {
  const state = useAsync(getConnection, []);
  const account = state.status === "success" ? (state.data.items[0] ?? null) : null;

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeading
        eyebrow="Email"
        title="Email Accounts"
        description="Connect and manage your email service providers."
        actions={state.status === "success" && account === null ? <ConnectButton /> : undefined}
      />

      <div aria-live="polite" className="sr-only">
        {state.status === "loading" ? "Loading email accounts" : ""}
      </div>

      {state.status === "loading" ? (
        <div className="grid gap-6 lg:grid-cols-3">
          <Card className="p-6">
            <Skeleton className="h-16 w-full rounded-tile" />
          </Card>
          <Card className="flex flex-col gap-4 p-6 lg:col-span-2">
            <Skeleton className="h-6 w-48" />
            <Skeleton className="h-32 w-full rounded-tile" />
          </Card>
        </div>
      ) : null}

      {state.status === "error" ? (
        <LoadFailure error={state.error} title="Couldn't load your email accounts" />
      ) : null}

      {state.status === "success" && account === null ? (
        <Card className="p-6">
          <EmptyState
            icon={<MailIcon className="size-6" />}
            title="No email account connected"
            description="Connect Google Workspace, Microsoft 365 or your own SMTP server to send campaigns and messages as your own address."
            action={<ConnectButton />}
          />
        </Card>
      ) : null}

      {state.status === "success" && account !== null ? (
        <div className="grid gap-6 lg:grid-cols-3">
          <section aria-label="Accounts" className="lg:col-span-1">
            <AccountList account={account} />
          </section>
          <section aria-label="Account details" className="lg:col-span-2">
            <AccountDetails account={account} />
          </section>
        </div>
      ) : null}
    </div>
  );
}
