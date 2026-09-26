import { useId, useState } from "react";
import type { ReactNode } from "react";

import { ApiError, getPlatformHost, useAsync } from "@opsgrids/client";
import { ArrowUpRight, CheckCircleIcon, ClockIcon, UserIcon, formatDuration } from "@opsgrids/ui";

import { getCustomer360 } from "@/api/customer360";
import type { Customer360, Money } from "@/api/customer360";
import { crmContactsUrl } from "@/lib/dashboard";
import { formatInstant } from "@/lib/format";
import { Button, Card, EmptyState, InlineAlert, Skeleton, StatusBadge, Tag } from "@/ui";

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
 */

const SECTIONS = ["crm", "orders", "finance", "support"] as const;
type SectionKey = (typeof SECTIONS)[number];

const SECTION_LABEL: Record<SectionKey, string> = {
  crm: "CRM",
  orders: "Orders",
  finance: "Finance",
  support: "Support",
};

/** Where "View" goes, per section. `null` means the app has no route for it, so there is no link. */
function sectionHref(key: SectionKey): string | null {
  switch (key) {
    case "crm":
      return crmContactsUrl();
    case "finance":
      return `${getPlatformHost("finance")}/invoices`;
    case "support":
      return `${getPlatformHost("support")}/queue`;
    case "orders":
      return null;
  }
}

function initials(name: string): string {
  const letters = name
    .split(/\s+/)
    .filter((part) => part !== "")
    .slice(0, 2)
    .map((part) => part.charAt(0).toUpperCase());
  return letters.join("");
}

/** Integer minor units in, a currency string out. The division is display-only, and the
 *  currency's own minor-unit count is read from `Intl` rather than assumed to be two. */
function formatMoney({ amount_minor, currency }: Money): string {
  try {
    const formatter = new Intl.NumberFormat(undefined, { style: "currency", currency });
    const digits = formatter.resolvedOptions().maximumFractionDigits ?? 2;
    return formatter.format(amount_minor / 10 ** digits);
  } catch {
    return `${amount_minor} ${currency}`;
  }
}

function formatDay(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, {
    day: "2-digit",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  });
}

function plural(count: number, one: string, many: string): string {
  return `${count} ${count === 1 ? one : many}`;
}

function isUnavailable(data: Customer360, key: SectionKey): boolean {
  return data[key] === null || data.unavailable.includes(key);
}

function ViewLink({ href, label }: { href: string; label: string }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      aria-label={label}
      className="inline-flex shrink-0 items-center gap-1 rounded-tile text-sm font-medium text-accent hover:text-accent-strong focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
    >
      View
      <ArrowUpRight className="size-3.5" aria-hidden />
    </a>
  );
}

function SectionRow({
  label,
  href,
  children,
}: {
  label: string;
  href: string | null;
  children: ReactNode;
}) {
  return (
    <li className="flex items-start justify-between gap-3 border-t border-line py-3 first:border-t-0">
      <div className="flex min-w-0 flex-col gap-1">
        <h3 className="text-sm font-semibold text-ink">{label}</h3>
        {children}
      </div>
      {href !== null ? <ViewLink href={href} label={`View ${label} records`} /> : null}
    </li>
  );
}

function NotAvailable() {
  return <p className="text-sm text-muted">Not available</p>;
}

function Line({ children }: { children: ReactNode }) {
  return <p className="text-sm text-muted">{children}</p>;
}

function SectionBody({ data, section }: { data: Customer360; section: SectionKey }) {
  const { crm, orders, finance, support } = data;

  if (section === "crm" && crm !== null) {
    return (
      <>
        <Line>
          {crm.stage !== null ? `Stage: ${crm.stage}` : "No stage set"} ·{" "}
          {plural(crm.open_opportunities, "open opportunity", "open opportunities")}
        </Line>
        {crm.hot ? (
          <span>
            <Tag tone="warning">Hot</Tag>
          </span>
        ) : null}
      </>
    );
  }
  if (section === "orders" && orders !== null) {
    return (
      <>
        <Line>
          {plural(orders.count_90d, "order", "orders")} in the last 90 days ·{" "}
          {formatMoney(orders.total_90d)}
        </Line>
        {orders.latest !== null ? (
          <Line>
            Latest {orders.latest.number} · {orders.latest.status} ·{" "}
            {formatMoney(orders.latest.total)}
          </Line>
        ) : null}
      </>
    );
  }
  if (section === "finance" && finance !== null) {
    return (
      <>
        <Line>
          {formatMoney(finance.outstanding)} outstanding ·{" "}
          {plural(finance.overdue_count, "overdue invoice", "overdue invoices")}
        </Line>
        {finance.latest_invoice !== null ? (
          <Line>
            Latest {finance.latest_invoice.number} · {finance.latest_invoice.status} · due{" "}
            {formatDay(finance.latest_invoice.due_on)}
          </Line>
        ) : null}
      </>
    );
  }
  if (section === "support" && support !== null) {
    return (
      <Line>
        {plural(support.open_tickets, "open ticket", "open tickets")} of {support.total_tickets}
      </Line>
    );
  }
  return <NotAvailable />;
}

function ProfileField({
  label,
  mono = false,
  children,
}: {
  label: string;
  mono?: boolean;
  children: ReactNode;
}) {
  return (
    <div className="flex justify-between gap-3">
      <dt className="shrink-0 text-muted">{label}</dt>
      <dd className={`min-w-0 break-all text-right text-ink ${mono ? "font-mono" : ""}`}>
        {children}
      </dd>
    </div>
  );
}

function Profile({ data }: { data: Customer360 }) {
  const headingId = useId();
  const { customer } = data;
  const place = [customer.company, customer.city].filter((part) => part !== null).join(" · ");
  return (
    <section aria-labelledby={headingId} className="flex flex-col gap-3">
      <div className="flex items-center gap-3">
        <span
          aria-hidden
          className="flex size-10 shrink-0 items-center justify-center rounded-full bg-accent/10 text-sm font-semibold text-accent"
        >
          {initials(customer.name)}
        </span>
        <div className="flex min-w-0 flex-col gap-1">
          <h3 id={headingId} className="truncate text-sm font-semibold text-ink">
            {customer.name}
          </h3>
          <span>
            <StatusBadge status={customer.status} label={customer.status === "active" ? "Active" : "Inactive"} />
          </span>
        </div>
      </div>

      <dl className="flex flex-col gap-1 text-sm">
        <ProfileField label="Phone" mono>
          {customer.phone}
        </ProfileField>
        <ProfileField label="Email" mono>
          {customer.email}
        </ProfileField>
        {place !== "" ? <ProfileField label="Company">{place}</ProfileField> : null}
        <ProfileField label="Customer since">{formatDay(customer.since)}</ProfileField>
        <ProfileField label="Owner">
          {customer.owner !== null ? customer.owner.name : "No owner assigned"}
        </ProfileField>
      </dl>

      {customer.tags.length > 0 ? (
        <ul aria-label="Tags" className="flex flex-wrap gap-2">
          {customer.tags.map((tag) => (
            <li key={tag}>
              <Tag>{tag}</Tag>
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
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
          className="inline-flex h-9 items-center gap-2 rounded-tile border border-line bg-surface px-3 text-sm font-medium text-ink hover:bg-surface-muted focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          Create invoice
          <ArrowUpRight className="size-3.5" aria-hidden />
        </a>
      </div>
    </section>
  );
}

function Loaded({ data }: { data: Customer360 }) {
  const modulesId = useId();
  return (
    <div className="flex flex-col gap-4">
      <Profile data={data} />
      <Channels data={data} />
      <section aria-labelledby={modulesId} className="flex flex-col">
        <h3 id={modulesId} className="sr-only">
          Across OpsGrids
        </h3>
        <ul className="flex flex-col">
          {SECTIONS.map((key) => (
            <SectionRow
              key={key}
              label={SECTION_LABEL[key]}
              href={isUnavailable(data, key) ? null : sectionHref(key)}
            >
              {isUnavailable(data, key) ? (
                <NotAvailable />
              ) : (
                <SectionBody data={data} section={key} />
              )}
            </SectionRow>
          ))}
        </ul>
      </section>
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
  const [open, setOpen] = useState(true);
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
