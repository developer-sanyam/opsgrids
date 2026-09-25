import { Fragment, useCallback, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { deleteContact, listContacts } from "@/api/client";
import type { Contact, ContactStatus } from "@/api/types";
import { useDirectory } from "@/data/directoryContext";
import { companyName, ownerName } from "@/data/lookups";
import { ArrowRightIcon, ChevronDown, cn, MailIcon, PhoneIcon, PlusIcon, useDebouncedValue, UsersIcon, useToast } from "@opsgrids/ui";
import { formatDate, formatRelative } from "@/lib/format";
import { Can, problemMessage, useAsync, useCan, useMutation } from "@opsgrids/client";
import {
  Avatar,
  Button,
  Card,
  ConfirmDialog,
  ContactStatusPill,
  EmptyState,
  ErrorState,
  Pagination,
  PageHeader,
  Pill,
  Select,
  SearchInput,
  Skeleton,
} from "@/ui";
import { ContactFormDialog } from "@/forms/ContactFormDialog";

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

function ContactDetail({
  contact,
  canWrite,
  onEdit,
  onDelete,
}: {
  contact: Contact;
  canWrite: boolean;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const { companies } = useDirectory();
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
          View {companyName(companies, contact.company_id)}
          <ArrowRightIcon className="size-4" />
        </Link>
        {canWrite ? (
          <div className="flex items-center gap-2 pt-1">
            <Button size="sm" variant="outline" onClick={onEdit}>
              Edit
            </Button>
            <Button size="sm" variant="ghost" className="text-negative" onClick={onDelete}>
              Delete
            </Button>
          </div>
        ) : null}
      </div>
    </div>
  );
}

export function ContactsPage() {
  const toast = useToast();
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebouncedValue(search);
  const [status, setStatus] = useState<StatusFilter>("all");
  const [sort, setSort] = useState<Sort>("name");
  const [expanded, setExpanded] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(25);
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<Contact | null>(null);
  const [deleting, setDeleting] = useState<Contact | null>(null);
  const directory = useDirectory();
  const { owners, companies } = directory;
  const canWrite = useCan("crm.book.contact.write", "OWN");

  const loader = useCallback(
    () => listContacts({ search: debouncedSearch, status, page, size }),
    [debouncedSearch, status, page, size],
  );
  const contacts = useAsync(loader, [debouncedSearch, status, page, size]);

  const remove = useMutation((contact: Contact) => deleteContact(contact.id), {
    onSuccess: (_result, contact) => {
      setDeleting(null);
      toast.success(`${contact.full_name} deleted.`);
      contacts.reload();
      directory.reload();
    },
  });

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

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Records"
        title="Contacts"
        actions={
          <Can permission="crm.book.contact.write" scope="OWN">
            <Button leadingIcon={<PlusIcon className="size-4" />} onClick={() => setCreating(true)}>
              New contact
            </Button>
          </Can>
        }
      />

      <Card className="flex flex-col gap-4 p-4 sm:p-5">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
          <SearchInput
            label="Search contacts"
            placeholder="Search by name, title or email…"
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              setPage(1);
            }}
            containerClassName="lg:max-w-xs"
          />
          <div className="flex items-center gap-1.5 overflow-x-auto -mx-1 px-1">
            {STATUS_FILTERS.map((f) => (
              <Pill
                key={f.value}
                active={status === f.value}
                onClick={() => {
                  setStatus(f.value);
                  setPage(1);
                }}
              >
                {f.label}
              </Pill>
            ))}
          </div>
          <Select
            label="Sort contacts"
            containerClassName="lg:ml-auto lg:w-56"
            value={sort}
            onChange={(e) => setSort(e.target.value as Sort)}
            options={[
              { value: "name", label: "Name A–Z" },
              { value: "recent", label: "Most recent activity" },
            ]}
          />
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
        {contacts.status === "error" ? (
          <ErrorState
            title="Couldn't load contacts"
            message={problemMessage(contacts.error)}
            onRetry={contacts.reload}
          />
        ) : null}
        {contacts.status === "success" ? (
          rows.length === 0 ? (
            <EmptyState
              icon={<UsersIcon className="size-6" />}
              title={search ? "No contacts match that search" : "No contacts yet"}
              description={
                search
                  ? "Try a shorter search term, or clear the filters to see them all."
                  : "Create the first one to get started."
              }
              action={
                search ? null : (
                  <Can permission="crm.book.contact.write" scope="OWN">
                    <Button size="sm" onClick={() => setCreating(true)}>
                      New contact
                    </Button>
                  </Can>
                )
              }
            />
          ) : (
            <>
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
                                {companyName(companies, contact.company_id)}
                              </Link>
                            </td>
                            <td className="py-3 pr-3 align-middle">
                              <ContactStatusPill status={contact.status} />
                            </td>
                            <td className="hidden py-3 pr-3 align-middle sm:table-cell">
                              <span className="inline-flex items-center gap-2 min-w-0">
                                <Avatar name={ownerName(owners, contact.owner_id)} size="sm" />
                                <span className="truncate text-ink-soft">
                                  {ownerName(owners, contact.owner_id)}
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
                                <ContactDetail
                                  contact={contact}
                                  canWrite={canWrite}
                                  onEdit={() => setEditing(contact)}
                                  onDelete={() => setDeleting(contact)}
                                />
                              </td>
                            </tr>
                          ) : null}
                        </Fragment>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <Pagination
                page={contacts.data.page}
                pages={contacts.data.pages}
                total={contacts.data.total}
                size={contacts.data.size}
                noun="contacts"
                onPageChange={setPage}
                onSizeChange={(n) => {
                  setSize(n);
                  setPage(1);
                }}
              />
            </>
          )
        ) : null}
      </Card>

      {creating ? (
        <ContactFormDialog
          contact={null}
          companies={companies}
          owners={owners}
          onOwnerCreated={() => directory.reload()}
          onClose={() => setCreating(false)}
          onSaved={(contact) => {
            setCreating(false);
            toast.success(`${contact.full_name} created.`);
            contacts.reload();
            directory.reload();
          }}
        />
      ) : null}

      {editing ? (
        <ContactFormDialog
          contact={editing}
          companies={companies}
          owners={owners}
          onOwnerCreated={() => directory.reload()}
          onClose={() => setEditing(null)}
          onSaved={(contact) => {
            setEditing(null);
            toast.success(`${contact.full_name} saved.`);
            contacts.reload();
            directory.reload();
          }}
        />
      ) : null}

      <ConfirmDialog
        open={deleting !== null}
        onClose={() => {
          setDeleting(null);
          remove.reset();
        }}
        onConfirm={() => deleting && void remove.mutate(deleting)}
        title="Delete this contact?"
        body={`${deleting?.full_name} is removed from the account book. This can't be undone.`}
        confirmLabel="Delete"
        pendingLabel="Deleting…"
        pending={remove.pending}
        {...(remove.error ? { error: problemMessage(remove.error) } : {})}
      />
    </div>
  );
}
