import { useId, useState } from "react";

import { ApiError, getPlatformHost, useAsync } from "@opsgrids/client";
import { ArrowUpRight, CheckCircleIcon, ClockIcon, Customer360View, UserIcon, formatDuration } from "@opsgrids/ui";

import { getCustomer360 } from "@/api/customer360";
import type { Customer360 } from "@/api/customer360";
import { crmContactsUrl } from "@/lib/dashboard";
import { formatInstant } from "@/lib/format";
import { Button, Card, EmptyState, InlineAlert, Skeleton } from "@/ui";

/**
 * The customer beside a conversation: who they are, whether they may be messaged, and what the
 * rest of the suite knows about them.
 *
 * One request (`GET /customers/{id}/360`) fills every section, and the service answers with
 * **partial results** — a section it could not read, or that the organization is not entitled to,
 * is `null` and named in `unavailable`. Each section reads that on its own and says "Not
 * available" without taking the others down. `phone` and `email` arrive masked and are shown
 * exactly as they arrive: nothing on this panel unmasks, and neither is a `tel:`/`mailto:` link
 * because a link to a masked value would dial nothing.
 *
 * Links go to routes that exist in the other apps today, by plain `href`; a section whose app has
 * no route for it (there is no orders screen for a customer) simply has no link.
 *
 * The profile and the section rows are the shared `Customer360View`; this panel keeps the fetch,
 * its card, its one live region, the failure messages and the Engage-only channel and quick
 * action blocks.
 */

type SectionKey = "crm" | "orders" | "finance" | "support";

const SECTION_LABEL: Record<SectionKey, string> = {
  crm: "CRM",
  orders: "Orders",
  finance: "Finance",
  support: "Support",
};

/** Where "View" goes, per section. `null` means the app has no route for it, so there is no link. */
function sectionHref(name: string): string | null {
  switch (name) {
    case "crm":
      return crmContactsUrl();
    case "finance":
      return `${getPlatformHost("finance")}/invoices`;
    case "support":
      return `${getPlatformHost("support")}/queue`;
    default:
      return null;
  }
}

function isUnavailable(data: Customer360, key: SectionKey): boolean {
  return data[key] === null || data.unavailable.includes(key);
}

function NotAvailable() {
  return <p className="text-sm text-muted">Not available</p>;
}

function windowLine(openUntil: string | null): string {
  if (openUntil === null) return "Closed";
  const remaining = new Date(openUntil).getTime() - Date.now();
  return remaining > 0 ? `Active (${formatDuration(remaining)} left)` : "Closed";
}

function Channels({ data }: { data: Customer360 }) {
  const headingId = useId();
  // A channel is unavailable when its summary is null or the service named it in `unavailable`
  // (under its own name, or under the whole `channels` object).
  const gone = (name: "whatsapp" | "email") =>
    data.unavailable.includes("channels") || data.unavailable.includes(name);
  const whatsapp = gone("whatsapp") ? null : data.channels.whatsapp;
  const email = gone("email") ? null : data.channels.email;
  return (
    <section aria-labelledby={headingId} className="flex flex-col gap-3">
      <h3 id={headingId} className="text-sm font-semibold text-ink">
        Channels
      </h3>

      <div className="flex flex-col gap-1 rounded-tile border border-line bg-surface-muted p-3">
        {whatsapp !== null ? (
          <>
            <p className="inline-flex items-center gap-2 text-sm font-medium text-ink">
              {whatsapp.opted_in ? (
                <CheckCircleIcon className="size-4 text-present" aria-hidden />
              ) : null}
              WhatsApp: {whatsapp.opted_in ? "Opted in" : "Not opted in"}
            </p>
            <p className="text-sm text-muted">
              {whatsapp.last_message_at !== null
                ? `Last message ${formatInstant(whatsapp.last_message_at)}`
                : "No messages yet"}
            </p>
            <p className="inline-flex items-center gap-2 text-sm text-muted">
              <ClockIcon className="size-3.5" aria-hidden />
              Customer window: {windowLine(whatsapp.window_open_until)}
            </p>
          </>
        ) : (
          <>
            <p className="text-sm font-medium text-ink">WhatsApp</p>
            <NotAvailable />
          </>
        )}
      </div>

      <div className="flex flex-col gap-1 rounded-tile border border-line bg-surface-muted p-3">
        {email !== null ? (
          <>
            <p className="inline-flex items-center gap-2 text-sm font-medium text-ink">
              {email.subscribed ? (
                <CheckCircleIcon className="size-4 text-present" aria-hidden />
              ) : null}
              Email: {email.subscribed ? "Subscribed" : "Unsubscribed"}
            </p>
            <p className="text-sm text-muted">
              {email.last_email_at !== null
                ? `Last email ${formatInstant(email.last_email_at)}`
                : "No emails yet"}
            </p>
          </>
        ) : (
          <>
            <p className="text-sm font-medium text-ink">Email</p>
            <NotAvailable />
          </>
        )}
      </div>
    </section>
  );
}

function QuickActions({ data }: { data: Customer360 }) {
  const headingId = useId();
  // Only actions with a route that exists today. Finance has `invoices/new`; nothing else the
  // design lists (task, order, ticket) has a create route in another app yet.
  if (isUnavailable(data, "finance")) return null;
  return (
    <section aria-labelledby={headingId} className="flex flex-col gap-2">
      <h3 id={headingId} className="text-sm font-semibold text-ink">
        Quick actions
      </h3>
      <div className="flex flex-wrap gap-2">
        <a
          href={`${getPlatformHost("finance")}/invoices/new`}
          target="_blank"
          rel="noreferrer"
          className="relative hit-44 inline-flex h-9 items-center gap-2 rounded-tile border border-line bg-surface px-3 text-sm font-medium text-ink hover:bg-surface-muted focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          Create invoice
          <ArrowUpRight className="size-3.5" aria-hidden />
        </a>
      </div>
    </section>
  );
}

function Loaded({ data }: { data: Customer360 }) {
  return (
    <div className="flex flex-col gap-4">
      <Customer360View data={data} sectionLabel={(name) => SECTION_LABEL[name as SectionKey] ?? name} sectionHref={sectionHref} />
      <Channels data={data} />
      <QuickActions data={data} />
    </div>
  );
}

/** Same silhouette as the loaded panel, so nothing shifts when the answer lands. */
function LoadingBody() {
  return (
    <div className="flex flex-col gap-4" aria-busy="true">
      <span className="sr-only">Loading customer details</span>
      <div className="flex items-center gap-3">
        <Skeleton className="size-10 rounded-full" />
        <Skeleton className="h-10 w-2/3" />
      </div>
      <Skeleton className="h-24 w-full" />
      <Skeleton className="h-24 w-full" />
      <Skeleton className="h-16 w-full" />
      <Skeleton className="h-16 w-full" />
      <Skeleton className="h-16 w-full" />
      <Skeleton className="h-16 w-full" />
    </div>
  );
}

/**
 * What a failed read says. A `403` and a `402` are the service's answer and are passed through in
 * its own words — the caller's role or the organization's plan is what has to change, so there is
 * no retry to offer (a 4xx does not fix itself). Anything else, including no response at all, is
 * worth trying again.
 */
/**
 * The short sentence the panel's live region says for a failed read. It is deliberately not the
 * alert's own title: the alert is for the eye and stays silent (`live="off"`), so a failure is
 * heard once, in a few words, and the detail is there to be read.
 */
function errorStatus(error: Error): string {
  if (error instanceof ApiError && error.status === 403) return "Access to customer details was refused.";
  if (error instanceof ApiError && error.status === 402) return "Customer details aren't part of your plan.";
  return "Customer details didn't load.";
}

function LoadError({ error, onRetry }: { error: Error; onRetry: () => void }) {
  if (error instanceof ApiError && error.status === 403) {
    return (
      <InlineAlert tone="warning" live="off" title="You don't have access to this customer's details">
        {error.message}
      </InlineAlert>
    );
  }
  if (error instanceof ApiError && error.status === 402) {
    return (
      <InlineAlert tone="warning" live="off" title="Customer 360 isn't included in your plan">
        {error.message}
      </InlineAlert>
    );
  }
  return (
    <div className="flex flex-col gap-3">
      <InlineAlert tone="danger" live="off" title="Couldn't load customer details">
        {error.message}
      </InlineAlert>
      <div>
        <Button variant="outline" size="sm" onClick={onRetry}>
          Try again
        </Button>
      </div>
    </div>
  );
}

/**
 * The panel's one live region. It says only that the details arrived, or what went wrong — never
 * the profile itself, which a screen reader would otherwise read out in full every time the
 * selected conversation changes. It stays mounted while the body loads, empty, so the change to
 * "loaded" is announced.
 */
function Customer360Body({ customerId }: { customerId: string }) {
  const state = useAsync(() => getCustomer360(customerId), [customerId]);
  const status =
    state.status === "success" ? "Customer details loaded" : state.status === "error" ? errorStatus(state.error) : "";

  return (
    <>
      {state.status === "loading" ? (
        <LoadingBody />
      ) : state.status === "error" ? (
        <LoadError error={state.error} onRetry={state.reload} />
      ) : (
        <Loaded data={state.data} />
      )}
      <p role="status" className="sr-only">
        {status}
      </p>
    </>
  );
}

/**
 * `customerId` is a conversation's `contact_id`. It is `null` for a number no CRM contact has been
 * matched to yet — an ordinary state, so the panel says so instead of guessing a customer.
 *
 * The body is not mounted while the panel is collapsed, so a collapsed panel makes no request.
 */
export function Customer360Panel({ customerId }: { customerId: string | null }) {
  // Open beside the thread on a wide screen; on a phone it starts collapsed, so the thread and the
  // reply box are what the screen opens on.
  const [open, setOpen] = useState(
    () => typeof window.matchMedia !== "function" || window.matchMedia("(min-width: 1024px)").matches,
  );
  const bodyId = useId();

  return (
    <aside
      aria-label="Customer 360"
      className={`w-full ${open ? "xl:w-1/3" : "xl:w-auto"} xl:shrink-0`}
    >
      <Card className="flex flex-col gap-4 p-4">
        <div className="flex items-center justify-between gap-3">
          <h2 className="text-base font-semibold text-ink">Customer 360</h2>
          <Button
            variant="outline"
            size="sm"
            aria-expanded={open}
            aria-controls={bodyId}
            onClick={() => setOpen((previous) => !previous)}
          >
            {open ? "Hide" : "Show"}
          </Button>
        </div>

        <div id={bodyId} hidden={!open}>
          {open ? (
            customerId === null ? (
              <EmptyState
                icon={<UserIcon className="size-6" />}
                title="No customer linked to this conversation"
                description="Once this number matches a CRM contact, their profile and history appear here."
              />
            ) : (
              <Customer360Body customerId={customerId} />
            )
          ) : null}
        </div>
      </Card>
    </aside>
  );
}
