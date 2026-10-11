import { useId } from "react";
import type { ReactNode } from "react";

import { ArrowUpRight } from "../icons";
import { formatMoney, initials } from "../lib/format";
import { StatusBadge } from "./StatusBadge";
import { Tag } from "./Tag";

export interface Customer360Money {
  amount_minor: number;
  currency: string;
}

/**
 * What the view reads from a customer 360 answer. Structural, so the service's own type (the
 * generated client type, or an app's hand-written one) is assignable to it without a mapping.
 * `phone` and `email` arrive masked; `null` or blank means none is on file.
 */
export interface Customer360ViewModel {
  customer: {
    id: string;
    name: string;
    company: string | null;
    since: string;
    status: string;
    tags: readonly string[];
    owner: { id: string; name: string } | null;
    phone: string | null;
    email: string | null;
    city: string | null;
  };
  crm: { stage: string | null; open_opportunities: number; hot: boolean } | null;
  orders: {
    count_90d: number;
    total_90d: Customer360Money;
    latest: { number: string; status: string; total: Customer360Money } | null;
  } | null;
  finance: {
    outstanding: Customer360Money;
    overdue_count: number;
    latest_invoice: { number: string; status: string; due_on: string } | null;
  } | null;
  support: { open_tickets: number; total_tickets: number } | null;
  /** Rendered by the app (consent and windows are channel-specific); the view does not read it. */
  channels: unknown;
  timeline: readonly unknown[];
  /** Section names the service could not read or the organization is not entitled to. */
  unavailable: readonly string[];
}

interface Customer360ViewProps {
  data: Customer360ViewModel;
  /** The section heading for a name: "crm" -> "CRM". Also names sections this view does not know. */
  sectionLabel: (name: string) => string;
  /** Where a section's "View" goes in its own app, or `null` for no link. */
  sectionHref?: ((name: string) => string | null) | undefined;
  /** Under the masked phone: the app's reveal control. */
  revealSlot?: ReactNode | undefined;
}

const SECTIONS = ["crm", "orders", "finance", "support"] as const;
type SectionName = (typeof SECTIONS)[number];

/** Names the service may put in `unavailable` that are not section rows here. */
const NOT_ROWS = new Set<string>([...SECTIONS, "customer", "channels", "whatsapp", "email", "timeline", "unavailable"]);

function money({ amount_minor, currency }: Customer360Money): string {
  return formatMoney(amount_minor, currency);
}

function formatDay(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { day: "2-digit", month: "short", year: "numeric", timeZone: "UTC" });
}

function plural(count: number, one: string, many: string): string {
  return `${count} ${count === 1 ? one : many}`;
}

function Line({ children }: { children: ReactNode }) {
  return <p className="text-sm text-muted">{children}</p>;
}

const NOT_AVAILABLE = <Line>Not available</Line>;

function SectionBody({ data, name }: { data: Customer360ViewModel; name: SectionName }) {
  const { crm, orders, finance, support } = data;
  if (name === "crm" && crm !== null) {
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
  if (name === "orders" && orders !== null) {
    return (
      <>
        <Line>
          {plural(orders.count_90d, "order", "orders")} in the last 90 days · {money(orders.total_90d)}
        </Line>
        {orders.latest !== null ? (
          <Line>
            Latest {orders.latest.number} · {orders.latest.status} · {money(orders.latest.total)}
          </Line>
        ) : null}
      </>
    );
  }
  if (name === "finance" && finance !== null) {
    return (
      <>
        <Line>
          {money(finance.outstanding)} outstanding · {plural(finance.overdue_count, "overdue invoice", "overdue invoices")}
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
  if (name === "support" && support !== null) {
    return <Line>{plural(support.open_tickets, "open ticket", "open tickets")} of {support.total_tickets}</Line>;
  }
  return NOT_AVAILABLE;
}

function SectionRow({ label, href, children }: { label: string; href: string | null; children: ReactNode }) {
  return (
    <li className="flex items-start justify-between gap-3 border-t border-line py-3 first:border-t-0">
      <div className="flex min-w-0 flex-col gap-1">
        <h3 className="text-sm font-semibold text-ink">{label}</h3>
        {children}
      </div>
      {href !== null ? (
        <a
          href={href}
          target="_blank"
          rel="noreferrer"
          aria-label={`View ${label} records`}
          className="inline-flex shrink-0 items-center gap-1 rounded-tile text-sm font-medium text-accent hover:text-accent-strong focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          View
          <ArrowUpRight className="size-3.5" aria-hidden />
        </a>
      ) : null}
    </li>
  );
}

function ProfileField({ label, mono = false, children }: { label: string; mono?: boolean; children: ReactNode }) {
  return (
    <div className="flex justify-between gap-3">
      <dt className="shrink-0 text-muted">{label}</dt>
      <dd className={`min-w-0 break-all text-right text-ink ${mono ? "font-mono" : ""}`}>{children}</dd>
    </div>
  );
}

const present = (value: string | null): value is string => value !== null && value.trim() !== "";

function Profile({ data, revealSlot }: { data: Customer360ViewModel; revealSlot: ReactNode }) {
  const headingId = useId();
  const { customer } = data;
  const place = [customer.company, customer.city].filter((part) => part !== null).join(" · ");
  const active = customer.status === "active";
  return (
    <section aria-labelledby={headingId} className="flex flex-col gap-3">
      <div className="flex items-center gap-3">
        <span
          aria-hidden
          className="flex size-10 shrink-0 items-center justify-center rounded-full bg-accent-soft text-sm font-semibold text-accent-ink"
        >
          {initials(customer.name)}
        </span>
        <div className="flex min-w-0 flex-col gap-1">
          <h3 id={headingId} className="break-words text-sm font-semibold text-ink">
            {customer.name}
          </h3>
          <span>
            <StatusBadge status={active ? "active" : "inactive"} label={active ? "Active" : "Inactive"} />
          </span>
        </div>
      </div>

      <dl className="flex flex-col gap-1 text-sm">
        {present(customer.phone) ? (
          <ProfileField label="Phone" mono>
            {customer.phone}
            {revealSlot !== undefined ? <span className="mt-1 flex justify-end font-sans">{revealSlot}</span> : null}
          </ProfileField>
        ) : null}
        {present(customer.email) ? (
          <ProfileField label="Email" mono>
            {customer.email}
          </ProfileField>
        ) : null}
        {place !== "" ? <ProfileField label="Company">{place}</ProfileField> : null}
        <ProfileField label="Customer since">{formatDay(customer.since)}</ProfileField>
        <ProfileField label="Owner">{customer.owner !== null ? customer.owner.name : "No owner assigned"}</ProfileField>
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

/**
 * A customer across the suite: the profile, then one row per section in a fixed order (CRM,
 * Orders, Finance, Support) and any section this view does not know yet, by name.
 *
 * **Never fetches and never fails whole.** The app loads the answer, owns its loading and error
 * states, and passes it in. A section that is `null` or named in `unavailable` says "Not
 * available" on its own row and the rest stay. Phone and email are shown masked, as they arrive,
 * and never as `tel:` or `mailto:` links; the app puts its reveal control in `revealSlot`.
 */
export function Customer360View({ data, sectionLabel, sectionHref, revealSlot }: Customer360ViewProps) {
  const modulesId = useId();
  const unavailable = (name: SectionName) => data[name] === null || data.unavailable.includes(name);
  // A section added to the service after this bundle (R4.4's `delivery`) is shown by name, not dropped.
  const extra = [...new Set([...data.unavailable, ...Object.keys(data)])].filter((name) => !NOT_ROWS.has(name));

  return (
    <div className="flex flex-col gap-4">
      <Profile data={data} revealSlot={revealSlot} />
      <section aria-labelledby={modulesId} className="flex flex-col">
        <h3 id={modulesId} className="sr-only">
          Across OpsGrids
        </h3>
        <ul className="flex flex-col">
          {SECTIONS.map((name) => {
            const gone = unavailable(name);
            return (
              <SectionRow key={name} label={sectionLabel(name)} href={gone ? null : (sectionHref?.(name) ?? null)}>
                {gone ? NOT_AVAILABLE : <SectionBody data={data} name={name} />}
              </SectionRow>
            );
          })}
          {extra.map((name) => (
            <SectionRow key={name} label={sectionLabel(name)} href={null}>
              {NOT_AVAILABLE}
            </SectionRow>
          ))}
        </ul>
      </section>
    </div>
  );
}
