import { Fragment, useCallback, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { listContacts } from "@/api/client";
import type { Contact, ContactStatus } from "@/api/types";
import { companyName, ownerName } from "@/data/lookups";
import { ArrowRightIcon, ChevronDown, MailIcon, PhoneIcon, UsersIcon, cn } from "@atmora/ui";
import { formatDate, formatRelative } from "@/lib/format";
import { useAsync } from "@atmora/client";
import {
  Avatar,
  Card,
  ContactStatusPill,
  EmptyState,
  ErrorState,
  PageHeader,
  Pill,
  SearchInput,
  Skeleton,
} from "@/ui";

type StatusFilter = ContactStatus | "all";
type Sort = "name" | "recent";

const STATUS_FILTERS: { value: StatusFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "customer", label: "Customers" },
  { value: "qualified", label: "Qualified" },
  { value: "lead", label: "Leads" },
  { value: "dormant", label: "Dormant" },
];

const NO_CONTACTS: Contact[] = [];

function ContactDetail({ contact }: { contact: Contact }) {
  return (
    <div className="grid grid-cols-1 gap-4 rounded-tile bg-ground/50 p-4 sm:grid-cols-2">
      <div className="flex flex-col gap-2 text-sm">
        <a
          href={`mailto:${contact.email}`}
          className="inline-flex items-center gap-2 text-ink-soft hover:text-accent transition-colors"
        >
          <MailIcon className="size-4 text-faint" />
          {contact.email}
        </a>
        <span className="inline-flex items-center gap-2 text-ink-soft">
          <PhoneIcon className="size-4 text-faint" />
          <span className="tnum">{contact.phone}</span>
        </span>
      </div>
      <div className="flex flex-col gap-2 text-sm sm:items-end">
        <span className="text-muted">
          Added <span className="tnum text-ink-soft">{formatDate(contact.created_at)}</span>
        </span>
        <Link
          to={`/companies/${contact.company_id}`}
          className="inline-flex items-center gap-1.5 font-semibold text-accent hover:text-accent-strong transition-colors"
        >
          View {companyName(contact.company_id)}
          <ArrowRightIcon className="size-4" />
        </Link>
      </div>
    </div>
  );
}

export function ContactsPage() {
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState<StatusFilter>("all");
  const [sort, setSort] = useState<Sort>("name");
  const [expanded, setExpanded] = useState<string | null>(null);

  const loader = useCallback(() => listContacts({ search, status }), [search, status]);
  const contacts = useAsync(loader, [search, status]);

  const rows = useMemo(() => {
    const items = contacts.status === "success" ? contacts.data.items : NO_CONTACTS;
    const sorted = [...items];
    if (sort === "name") sorted.sort((a, b) => a.full_name.localeCompare(b.full_name));
    else
      sorted.sort(
        (a, b) => Date.parse(b.last_activity_at ?? "0") - Date.parse(a.last_activity_at ?? "0"),
      );
    return sorted;
  }, [contacts, sort]);

  const total = contacts.status === "success" ? contacts.data.total : 0;

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Records"
        title="Contacts"
        actions={
          <span className="inline-flex h-11 items-center gap-2 rounded-full bg-surface px-4 text-sm font-semibold shadow-card">
            <UsersIcon className="size-4 text-muted" />
            <span className="tnum">{total}</span>
            <span className="font-medium text-muted">people</span>
          </span>
        }
      />

      <Card className="flex flex-col gap-4 p-4 sm:p-5">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
          <SearchInput
            label="Search contacts"
            placeholder="Search by name, title or email…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            containerClassName="lg:max-w-xs"
          />
          <div className="flex items-center gap-1.5 overflow-x-auto -mx-1 px-1">
            {STATUS_FILTERS.map((f) => (
              <Pill key={f.value} active={status === f.value} onClick={() => setStatus(f.value)}>
                {f.label}
              </Pill>
            ))}
          </div>
          <label className="ml-auto inline-flex h-11 shrink-0 items-center rounded-full bg-ground px-4">
            <span className="sr-only">Sort contacts</span>
            <select
              value={sort}
              onChange={(e) => setSort(e.target.value as Sort)}
              className="h-full cursor-pointer bg-transparent pr-1 text-sm font-semibold text-ink focus:outline-none"
            >
              <option value="name">Name A–Z</option>
              <option value="recent">Most recent activity</option>
            </select>
          </label>
        </div>
      </Card>

      <Card className="p-2 sm:p-4">
        {contacts.status === "loading" ? (
          <div className="flex flex-col gap-3 p-2">
            {Array.from({ length: 8 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full rounded-tile" />
            ))}
          </div>
        ) : null}
        {contacts.status === "error" ? <ErrorState message={contacts.error.message} /> : null}
        {contacts.status === "success" ? (
          rows.length === 0 ? (
            <EmptyState
              icon={<UsersIcon className="size-6" />}
              title="No matching contacts"
              description="No people match this search or status. Clear the filters to see them all."
            />
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full border-collapse text-sm">
                <caption className="sr-only">All contacts</caption>
                <thead>
                  <tr className="border-b border-line text-xs font-semibold uppercase tracking-wide text-faint">
                    <th scope="col" className="py-3 pl-2 pr-3 text-left">Contact</th>
                    <th scope="col" className="hidden py-3 pr-3 text-left md:table-cell">Company</th>
                    <th scope="col" className="py-3 pr-3 text-left">Status</th>
                    <th scope="col" className="hidden py-3 pr-3 text-left sm:table-cell">Owner</th>
                    <th scope="col" className="py-3 pr-3 text-right">Last activity</th>
                    <th scope="col" className="w-10 py-3 pr-2 text-right">
                      <span className="sr-only">Expand</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((contact) => {
                    const isOpen = expanded === contact.id;
                    return (
                      <Fragment key={contact.id}>
                        <tr
                          className={cn(
                            "border-b border-line/70 transition-colors",
                            isOpen ? "bg-ground/50" : "hover:bg-ground/60",
                          )}
                        >
                          <td className="py-3 pl-2 pr-3 align-middle">
                            <button
                              type="button"
                              onClick={() => setExpanded(isOpen ? null : contact.id)}
                              aria-expanded={isOpen}
                              className="flex items-center gap-3 text-left"
                            >
                              <Avatar name={contact.full_name} size="sm" />
                              <div className="flex min-w-0 flex-col leading-tight">
                                <span className="truncate font-semibold text-ink">
                                  {contact.full_name}
                                </span>
                                <span className="truncate text-xs text-muted">{contact.title}</span>
                              </div>
                            </button>
                          </td>
                          <td className="hidden py-3 pr-3 align-middle md:table-cell">
                            <Link
                              to={`/companies/${contact.company_id}`}
                              className="font-medium text-ink-soft hover:text-accent transition-colors"
                            >
                              {companyName(contact.company_id)}
                            </Link>
                          </td>
                          <td className="py-3 pr-3 align-middle">
                            <ContactStatusPill status={contact.status} />
                          </td>
                          <td className="hidden py-3 pr-3 align-middle sm:table-cell">
                            <span className="inline-flex items-center gap-2 min-w-0">
                              <Avatar name={ownerName(contact.owner_id)} size="sm" />
                              <span className="truncate text-ink-soft">
                                {ownerName(contact.owner_id)}
                              </span>
                            </span>
                          </td>
                          <td className="py-3 pr-3 text-right align-middle">
                            <span className="tnum text-muted">
                              {formatRelative(contact.last_activity_at)}
                            </span>
                          </td>
                          <td className="py-3 pr-2 text-right align-middle">
                            <button
                              type="button"
                              onClick={() => setExpanded(isOpen ? null : contact.id)}
                              aria-label={isOpen ? "Collapse details" : "Expand details"}
                              aria-expanded={isOpen}
                              className="grid size-8 place-items-center rounded-full text-faint hover:bg-ground hover:text-ink transition-colors"
                            >
                              <ChevronDown
                                className={cn("size-4 transition-transform", isOpen && "rotate-180")}
                              />
                            </button>
                          </td>
                        </tr>
                        {isOpen ? (
                          <tr className="border-b border-line/70">
                            <td colSpan={6} className="px-2 pb-3">
                              <ContactDetail contact={contact} />
                            </td>
                          </tr>
                        ) : null}
                      </Fragment>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )
        ) : null}
      </Card>
    </div>
  );
}
