import { useCallback, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { useAsync } from "@opsgrids/client";
import { SendIcon, UsersIcon } from "@opsgrids/ui";

import { listDomains, listSenders } from "@/api/client";
import type { DomainOut, SenderOut } from "@/api/types";
import { formatInstant } from "@/lib/format";
import {
  Announcer,
  Button,
  Card,
  EmptyState,
  InlineAlert,
  LoadFailure,
  PageHeader,
  SenderStatusTag,
  Skeleton,
  useAnnounce,
} from "@/ui";
import { SenderFormDialog } from "./SenderFormDialog";
import { TestSendDialog } from "./TestSendDialog";

/**
 * The named identities mail can go out as, and the test send that makes one usable.
 *
 * `usable` is the service's own derivation of `verified_at` and is rendered as it arrives. The
 * client never computes it: a sender is usable because a test message actually reached the
 * provider, and that is a fact only the server watched happen. **An untested sender says so in
 * words on its own row** — the gate is the point of this screen, and a pill nobody reads is not
 * an explanation of it.
 *
 * The domain is shown beside each address rather than assumed from the string after the `@`.
 * Which domain a sender belongs to is `domain_id` — a row — and a sender whose address and domain
 * had drifted apart would be exactly the case worth seeing.
 */

function SenderRow({
  sender,
  domainName,
  onTest,
}: {
  sender: SenderOut;
  domainName: string;
  onTest: () => void;
}) {
  return (
    <tr className="border-t border-line align-top">
      <th scope="row" className="py-3 pr-4 text-left">
        <span className="block text-sm font-bold text-ink">{sender.name}</span>
        <span className="block font-mono text-xs break-all text-muted">{sender.address}</span>
      </th>
      <td className="py-3 pr-4 text-sm text-ink">{domainName}</td>
      <td className="py-3 pr-4 text-sm text-muted">
        {sender.department === "" ? "—" : sender.department}
      </td>
      <td className="py-3 pr-4 font-mono text-xs break-all text-muted">
        {sender.reply_to === "" ? (
          <span className="font-sans">Replies go to the address</span>
        ) : (
          sender.reply_to
        )}
      </td>
      <td className="py-3 pr-4">
        <SenderStatusTag usable={sender.usable} />
        <span className="mt-1 block text-xs text-muted">
          {sender.verified_at === null
            ? "No campaign can send as this address until a test message is delivered."
            : `Delivered ${formatInstant(sender.verified_at)}`}
        </span>
      </td>
      <td className="py-3 whitespace-nowrap">
        <Button
          variant="outline"
          size="sm"
          onClick={onTest}
          leadingIcon={<SendIcon className="size-4" />}
        >
          {/* The label says what pressing it does, and the accessible name says which row it
              does it on — five identical "Send test" buttons are five identical link texts. */}
          {sender.usable ? "Test again" : "Send test"}
          <span className="sr-only"> as {sender.address}</span>
        </Button>
      </td>
    </tr>
  );
}

const NO_SENDERS: SenderOut[] = [];
const NO_DOMAINS: DomainOut[] = [];

function Senders() {
  const announce = useAnnounce();
  const [attempt, setAttempt] = useState(0);
  const [adding, setAdding] = useState(false);
  const [testing, setTesting] = useState<SenderOut | null>(null);
  /* Senders a test send has rewritten since the last read. The route answers with the whole
     sender — `verified_at` and the `usable` derived from it — so the row turns over on the
     provider's answer rather than on a second round trip. */
  const [tested, setTested] = useState<Record<string, SenderOut>>({});

  const loader = useCallback(() => Promise.all([listSenders(), listDomains()]), []);
  const state = useAsync(loader, [attempt]);

  const loaded = state.status === "success" ? state.data[0].items : NO_SENDERS;
  const domains = state.status === "success" ? state.data[1].items : NO_DOMAINS;
  const senders = useMemo(
    () => loaded.map((sender) => tested[sender.id] ?? sender),
    [loaded, tested],
  );

  /* Resolved by id, so a sender on a domain this page did not receive reads as "unknown domain"
     rather than silently borrowing the name of whichever row happened to sort first. */
  const domainNames = useMemo(() => new Map(domains.map((d) => [d.id, d.domain])), [domains]);

  // The writer refuses a sender on an unverified domain, so an empty list here is not a gap in
  // this screen — it is the previous step, unfinished.
  const verified = useMemo(() => domains.filter((d) => d.status === "verified"), [domains]);

  function reload(message: string) {
    announce(message);
    setAdding(false);
    setTested({});
    setAttempt((current) => current + 1);
  }

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Email"
        title="Senders"
        description="The name and address a message arrives from. A sender becomes usable once a test message has actually been delivered through your provider."
        actions={
          state.status === "success" && verified.length > 0 ? (
            <Button onClick={() => setAdding(true)}>Add a sender</Button>
          ) : null
        }
      />

      {state.status === "loading" ? (
        <Card className="flex flex-col gap-4 p-6">
          <Skeleton className="h-6 w-48" />
          <Skeleton className="h-32 w-full rounded-tile" />
        </Card>
      ) : null}

      {state.status === "error" ? (
        <LoadFailure error={state.error} title="Couldn't load your senders" />
      ) : null}

      {state.status === "success" && verified.length === 0 ? (
        <InlineAlert
          tone="warning"
          live="off"
          title="No verified domain to send from yet"
          action={
            <Link
              to="/domains"
              className="inline-flex h-9 items-center rounded-full px-3 text-sm font-semibold text-accent-ink transition-colors hover:bg-accent-soft focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
            >
              Open Domains
            </Link>
          }
        >
          A sender lives on a domain whose DNS has been proven, so there is nothing to add one to
          until a domain verifies.
        </InlineAlert>
      ) : null}

      {state.status === "success" && senders.length === 0 ? (
        <Card className="p-6">
          <EmptyState
            icon={<UsersIcon className="size-6" />}
            title="No senders yet"
            description="Add a sender on a verified domain — a shared address like support@, or a person who writes to customers directly."
            {...(verified.length > 0
              ? { action: <Button onClick={() => setAdding(true)}>Add a sender</Button> }
              : {})}
          />
        </Card>
      ) : null}

      {senders.length > 0 ? (
        <Card className="p-6">
          <div className="relative overflow-x-auto">
            <table className="w-full min-w-[60rem] border-collapse text-left">
              <caption className="sr-only">
                Senders, their domain and whether a test message has been delivered
              </caption>
              <thead>
                <tr className="text-xs font-bold uppercase tracking-wider text-faint">
                  <th scope="col" className="pb-2 pr-4">
                    Sender
                  </th>
                  <th scope="col" className="pb-2 pr-4">
                    Domain
                  </th>
                  <th scope="col" className="pb-2 pr-4">
                    Department
                  </th>
                  <th scope="col" className="pb-2 pr-4">
                    Reply-to
                  </th>
                  <th scope="col" className="pb-2 pr-4">
                    Can it send?
                  </th>
                  <th scope="col" className="pb-2">
                    <span className="sr-only">Test send</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {senders.map((sender) => (
                  <SenderRow
                    key={sender.id}
                    sender={sender}
                    domainName={domainNames.get(sender.domain_id) ?? "Unknown domain"}
                    onTest={() => setTesting(sender)}
                  />
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      ) : null}

      <SenderFormDialog
        open={adding}
        domains={verified}
        onClose={() => setAdding(false)}
        onSaved={(_sender, message) => reload(message)}
      />

      <TestSendDialog
        sender={testing}
        onClose={() => setTesting(null)}
        onSent={(sender, message) => {
          setTested((current) => ({ ...current, [sender.id]: sender }));
          setTesting(null);
          announce(message);
        }}
      />
    </div>
  );
}

/** The screen's single polite live region — both dialogs announce their result into it. */
export function SendersPage() {
  return (
    <Announcer>
      <Senders />
    </Announcer>
  );
}
