import type { ReactNode } from "react";

import { problemMessage, useAsync, useEntitlement } from "@opsgrids/client";
import { CheckCircleIcon, ClockIcon, MessageIcon } from "@opsgrids/ui";

import { getMessaging360 } from "@/api/customer360";
import type { Messaging360 } from "@/api/customer360";
import { formatDate, formatRelative } from "@/lib/format";
import { Card, EmptyState, ErrorState, Skeleton } from "@/ui";

function windowLine(openUntil: string | null): string {
  return openUntil !== null && Date.parse(openUntil) > Date.now() ? `Open until ${formatDate(openUntil)}` : "Closed";
}

function Channel({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-1 rounded-tile border border-line bg-surface-muted p-3">
      <p className="text-sm font-medium text-ink">{title}</p>
      {children}
    </div>
  );
}

function Loaded({ data }: { data: Messaging360 }) {
  // A channel is gone when its summary is null or the service named it (or all `channels`).
  const gone = (name: "whatsapp" | "email") => data.unavailable.includes("channels") || data.unavailable.includes(name);
  const whatsapp = gone("whatsapp") ? null : data.channels.whatsapp;
  const email = gone("email") ? null : data.channels.email;
  if (whatsapp === null && email === null && data.timeline.length === 0) {
    return (
      <EmptyState
        icon={<MessageIcon className="size-6" />}
        title="No messaging yet"
        description="Messages and emails with this contact appear here."
      />
    );
  }
  return (
    <div className="flex flex-col gap-4">
      <div className="grid gap-3 sm:grid-cols-2">
        <Channel title="WhatsApp">
          {whatsapp === null ? (
            <p className="text-sm text-muted">Not available</p>
          ) : (
            <>
              <p className="inline-flex items-center gap-2 text-sm text-ink">
                {whatsapp.opted_in ? <CheckCircleIcon className="size-4 text-present" aria-hidden /> : null}
                {whatsapp.opted_in ? "Opted in" : "Not opted in"}
              </p>
              <p className="inline-flex items-center gap-2 text-sm text-muted">
                <ClockIcon className="size-3.5" aria-hidden />
                24-hour window: {windowLine(whatsapp.window_open_until)}
              </p>
              <p className="text-sm text-muted">
                {whatsapp.last_message_at !== null
                  ? `Last message ${formatRelative(whatsapp.last_message_at)}`
                  : "No messages yet"}
                {" · "}
                {whatsapp.conversations} {whatsapp.conversations === 1 ? "conversation" : "conversations"}
              </p>
            </>
          )}
        </Channel>
        <Channel title="Email">
          {email === null ? (
            <p className="text-sm text-muted">Not available</p>
          ) : (
            <>
              <p className="inline-flex items-center gap-2 text-sm text-ink">
                {email.subscribed ? <CheckCircleIcon className="size-4 text-present" aria-hidden /> : null}
                {email.subscribed ? "Subscribed" : "Unsubscribed"}
              </p>
              <p className="text-sm text-muted">
                {email.last_email_at !== null ? `Last email ${formatRelative(email.last_email_at)}` : "No emails yet"}
              </p>
            </>
          )}
        </Channel>
      </div>
      {data.timeline.length > 0 ? (
        <ul className="flex flex-col divide-y divide-line">
          {data.timeline.map((entry, index) => (
            <li key={`${entry.at}-${index}`} className="flex items-start justify-between gap-3 py-3">
              <span className="min-w-0 break-words text-sm text-ink">{entry.title}</span>
              <span className="shrink-0 text-xs text-muted">{formatRelative(entry.at)}</span>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function Body({ contactId }: { contactId: string }) {
  const state = useAsync(() => getMessaging360(contactId), [contactId]);
  const status = state.status === "success" ? "Messaging loaded" : state.status === "error" ? "Messaging didn't load." : "";
  return (
    <>
      {state.status === "loading" ? (
        <div aria-busy="true" className="flex flex-col gap-3">
          <span className="sr-only">Loading messaging</span>
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-16 w-full" />
        </div>
      ) : state.status === "error" ? (
        <ErrorState title="Couldn't load messaging" message={problemMessage(state.error)} onRetry={state.reload} />
      ) : (
        <Loaded data={state.data} />
      )}
      <p role="status" className="sr-only">
        {status}
      </p>
    </>
  );
}

/** Hidden unless the organization has Engage; the 360 itself is still gated by the server. */
export function MessagingCard({ contactId }: { contactId: string }) {
  const entitled = useEntitlement("engage");
  if (!entitled) return null;
  return (
    <Card className="flex flex-col gap-4 p-4 sm:p-6">
      <h2 className="text-base font-semibold text-ink">Messaging</h2>
      <Body contactId={contactId} />
    </Card>
  );
}
