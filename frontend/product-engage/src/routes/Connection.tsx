import { useCallback, useState } from "react";
import { Link } from "react-router-dom";

import { useAsync } from "@opsgrids/client";

import { getConnection } from "@/api/client";
import type { ConnectionOut } from "@/api/types";
import { connectionKindLabel, formatNumber } from "@/lib/format";
import {
  Announcer,
  Card,
  ConnectionStatusTag,
  ErrorState,
  InlineAlert,
  PageHeader,
  Skeleton,
} from "@/ui";
import { ConnectionWizard } from "./ConnectionWizard";

/**
 * How this organization sends email — and, until it does, the wizard that decides it.
 *
 * **Nothing on this screen is derived from the credential, and nothing ever may be.** The app
 * password is write-only on the wire: `ConnectionOut` has no field for it, masked or otherwise,
 * and the sealed DKIM private key has no field at all. A last-four, a length or a row of dots
 * would each be the secret with a hint attached, reconstructed here from data the service
 * deliberately withheld. The note in the panel says so to the customer, because "why can't I see
 * what I entered" is a reasonable question with a real answer.
 */

/** One label/value row. `mono` for values a person compares character by character. */
function Detail({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex flex-col gap-1 border-t border-line py-3 first:border-t-0 first:pt-0 sm:flex-row sm:items-baseline sm:gap-4">
      <dt className="w-56 shrink-0 text-sm font-semibold text-muted">{label}</dt>
      <dd className={`min-w-0 break-words text-sm text-ink ${mono ? "font-mono tnum" : ""}`}>
        {value}
      </dd>
    </div>
  );
}

function ConnectionPanel({ connection }: { connection: ConnectionOut }) {
  return (
    <Card className="flex flex-col gap-5 p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-lg font-bold tracking-tight">
          {connectionKindLabel(connection.kind)}
        </h2>
        <ConnectionStatusTag status={connection.status} />
      </div>

      <dl className="flex flex-col">
        <Detail label="Signed in as" value={connection.username} mono />
        <Detail label="Submission host" value={`${connection.host}:${connection.port}`} mono />
        <Detail
          label="Daily limit"
          value={`${formatNumber(connection.daily_cap)} messages`}
        />
      </dl>

      <InlineAlert
        tone="info"
        title="The password you entered is not shown here, and cannot be"
        live="off"
      >
        It is sealed when it is saved and read only by the server that opens the connection.
        Nothing sends it back — not masked, not shortened.
      </InlineAlert>

      {/* One connection per organization, and the service refuses a second. There is no route
          that removes this one, so there is no control here that pretends to: telling a customer
          to "just reconnect" would walk them into a refusal with their new password in hand. */}
      <InlineAlert tone="warning" title="Replacing this connection isn't possible from here yet" live="off">
        An organization gets one connection and the service refuses a second, so a new app
        password needs this one removed first — which nothing in Engage can do today.
      </InlineAlert>
    </Card>
  );
}

function Connection() {
  const loader = useCallback(() => getConnection(), []);
  const state = useAsync(loader, []);
  /* The wizard's answer, held so the screen turns over the moment it succeeds rather than after a
     second round trip. `POST` returns the whole `ConnectionOut`, so there is nothing a re-read
     would add. */
  const [justConnected, setJustConnected] = useState<ConnectionOut | null>(null);
  // A page of at most one: an empty page is "no provider connected", which is the state a
  // customer who has just activated Engage is genuinely in.
  const loaded = state.status === "success" ? (state.data.items[0] ?? null) : null;
  const connection = justConnected ?? loaded;

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Email"
        title="Connections"
        description="Engage sends through your organization's own provider, so replies land in your mailboxes and your sending reputation stays yours."
      />

      {state.status === "loading" ? (
        <Card className="flex flex-col gap-5 p-6">
          <Skeleton className="h-6 w-48" />
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-2/3" />
          <Skeleton className="h-20 w-full rounded-tile" />
        </Card>
      ) : null}

      {state.status === "error" ? (
        <Card className="p-6">
          <ErrorState message={state.error.message} title="Couldn't load the connection" />
        </Card>
      ) : null}

      {justConnected !== null ? (
        <InlineAlert
          tone="success"
          title={`${connectionKindLabel(justConnected.kind)} is connected`}
          action={
            <Link
              to="/domains"
              className="inline-flex h-9 items-center rounded-full px-3 text-sm font-semibold text-accent-ink transition-colors hover:bg-accent-soft focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
            >
              Add a domain
            </Link>
          }
        >
          Mail can leave as soon as a domain is verified and a sender has passed its test.
        </InlineAlert>
      ) : null}

      {/* The unconnected state is the wizard itself. An empty panel saying "no provider
          connected" above the form that connects one is the same sentence twice. */}
      {state.status === "success" && connection === null ? (
        <ConnectionWizard onConnected={setJustConnected} />
      ) : null}

      {connection !== null ? <ConnectionPanel connection={connection} /> : null}
    </div>
  );
}

/** The screen's single polite live region — the wizard and everything under it announce into it. */
export function ConnectionPage() {
  return (
    <Announcer>
      <Connection />
    </Announcer>
  );
}
