import { useCallback, useEffect, useState } from "react";

import { Can, useAsync, useCan } from "@opsgrids/client";
import { FileTextIcon, UserPlusIcon, UsersIcon } from "@opsgrids/ui";

import { deleteContact, listContacts } from "@/api/client";
import type { ContactOut } from "@/api/types";
import { downloadCsv } from "@/lib/csv";
import { failureMessage } from "@/lib/errors";
import { formatInstant } from "@/lib/format";
import {
  Announcer,
  Button,
  Card,
  ConfirmDialog,
  DataTable,
  EmptyState,
  LoadFailure,
  PageHeader,
  Pagination,
  RowActions,
  SearchInput,
  Skeleton,
  useAnnounce,
  useDebouncedValue,
} from "@/ui";
import type { Column, TableSelection } from "@/ui";
import { ContactFormDialog } from "./ContactFormDialog";
import { ContactImportDialog } from "./ContactImportDialog";

/**
 * The organization's contacts — addresses masked everywhere, exactly as `_contact_out` returns
 * them. There is no client-side mask, no cache of a plaintext address and no field this screen
 * could render one from even if it tried: `ContactOut.email` **is** the mask.
 *
 * Search is the service's own rule, said plainly rather than left to be discovered by a query
 * that quietly returns nothing: a name substring, or an exact email address. Partial email search
 * cannot exist here — the address is sealed at rest, and matching inside it would mean decrypting
 * every row on every keystroke.
 */

const NO_CONTACTS: ContactOut[] = [];

function Contacts() {
  const announce = useAnnounce();
  const canWrite = useCan("engage.audience.contact.write", "ORG");
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebouncedValue(search);
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(25);
  const [adding, setAdding] = useState(false);
  const [importing, setImporting] = useState(false);
  const [editing, setEditing] = useState<ContactOut | null>(null);
  const [deleting, setDeleting] = useState<ContactOut | null>(null);
  const [deleteFailure, setDeleteFailure] = useState<string | null>(null);
  const [deletePending, setDeletePending] = useState(false);
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());

  const loader = useCallback(
    () => listContacts({ search: debouncedSearch, page, size }),
    [debouncedSearch, page, size],
  );
  const state = useAsync(loader, [debouncedSearch, page, size]);
  const contacts = state.status === "success" ? state.data.items : NO_CONTACTS;

  // Deleting the last row on a page beyond the first empties that page without emptying the
  // list — stepping back is what a person would do by hand, and it's what keeps this from
  // looking like "no contacts yet" when there are, in fact, contacts on the page before it.
  useEffect(() => {
    if (state.status === "success" && contacts.length === 0 && page > 1) {
      setPage(page - 1);
    }
  }, [state.status, contacts.length, page]);

  function reload(message: string) {
    announce(message);
    setAdding(false);
    setEditing(null);
    state.reload();
  }

  async function confirmDelete() {
    if (deleting === null || deletePending) return;
    setDeletePending(true);
    setDeleteFailure(null);
    try {
      await deleteContact(deleting.id);
      const name = deleting.name === "" ? deleting.email : deleting.name;
      setDeleting(null);
      reload(`${name} deleted.`);
    } catch (caught) {
      setDeleteFailure(failureMessage(caught));
    } finally {
      setDeletePending(false);
    }
  }

  const columns: Column<ContactOut>[] = [
    {
      key: "contact",
      header: "Contact",
      mobile: "title",
      render: (contact) => (
        <div className="flex flex-col">
          <span className="text-sm font-bold text-ink">{contact.name === "" ? "—" : contact.name}</span>
          <span className="font-mono text-xs text-muted">{contact.email}</span>
        </div>
      ),
    },
    { key: "created", header: "Added", render: (contact) => <span className="text-muted">{formatInstant(contact.created_at)}</span> },
    { key: "updated", header: "Updated", render: (contact) => <span className="text-muted">{formatInstant(contact.updated_at)}</span> },
    {
      key: "actions",
      header: "",
      align: "right",
      mobile: "actions",
      render: (contact) => (
        <RowActions
          label={contact.name === "" ? contact.email : contact.name}
          actions={[
            { label: "Edit", onSelect: () => setEditing(contact), hidden: !canWrite },
            { label: "Delete", tone: "danger", onSelect: () => setDeleting(contact), hidden: !canWrite },
          ]}
        />
      ),
    },
  ];

  const contactSelection: TableSelection<ContactOut> | undefined =
    state.status === "success"
      ? {
          selected,
          onToggle: (key, next) =>
            setSelected((prev) => {
              const nextSet = new Set(prev);
              if (next) nextSet.add(key);
              else nextSet.delete(key);
              return nextSet;
            }),
          onToggleAll: (next) => setSelected(next ? new Set(contacts.map((c) => c.id)) : new Set()),
          label: (c) => (c.name === "" ? c.email : c.name),
        }
      : undefined;

  function exportSelectedContacts() {
    const chosen = contacts.filter((c) => selected.has(c.id));
    downloadCsv(
      "contacts.csv",
      ["Name", "Email", "Added", "Updated"],
      chosen.map((c) => [c.name, c.email, formatInstant(c.created_at), formatInstant(c.updated_at)]),
    );
  }

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Audience"
        title="Contacts"
        description="Who Engage may reach. Addresses are sealed at rest and only ever shown masked, exactly as the service returns them."
        actions={
          <Can permission="engage.audience.contact.write" scope="ORG">
            <div className="flex gap-2">
              <Button
                variant="outline"
                onClick={() => setImporting(true)}
                leadingIcon={<FileTextIcon className="size-4" />}
              >
                Import
              </Button>
              <Button
                onClick={() => setAdding(true)}
                leadingIcon={<UserPlusIcon className="size-4" />}
              >
                Add a contact
              </Button>
            </div>
          </Can>
        }
      />

      <Card className="flex flex-col gap-4 p-4 sm:p-5">
        <SearchInput
          label="Search contacts"
          placeholder="Search by name, or paste an exact email address"
          value={search}
          onChange={(event) => {
            setSearch(event.target.value);
            setPage(1);
          }}
          containerClassName="sm:max-w-sm"
        />
      </Card>

      {state.status === "loading" ? (
        <Card className="flex flex-col gap-4 p-6">
          <Skeleton className="h-6 w-48" />
          <Skeleton className="h-32 w-full rounded-tile" />
        </Card>
      ) : null}

      {state.status === "error" ? (
        <LoadFailure error={state.error} title="Couldn't load your contacts" />
      ) : null}

      {state.status === "success" && contacts.length === 0 ? (
        <Card className="p-6">
          <EmptyState
            icon={<UsersIcon className="size-6" />}
            title={debouncedSearch.trim() === "" ? "No contacts yet" : "No contacts match that search"}
            description={
              debouncedSearch.trim() === ""
                ? "Add a contact by hand, or import a spreadsheet of up to 1,000 at once."
                : `Nothing matches “${debouncedSearch.trim()}”. Clear the search to see everyone.`
            }
          />
        </Card>
      ) : null}

      {state.status === "success" && contacts.length > 0 ? (
        <Card className="p-6">
          <DataTable
            caption="Contacts, their email mask and when each changed"
            rows={contacts}
            rowKey={(contact) => contact.id}
            columns={columns}
            mobileCard="auto"
            selection={contactSelection}
            bulkActions={
              <Button variant="outline" size="sm" onClick={exportSelectedContacts}>
                Export selected
              </Button>
            }
          />
          <Pagination
            page={state.data.page}
            pages={state.data.pages}
            total={state.data.total}
            size={state.data.size}
            noun="contacts"
            onPageChange={setPage}
            onSizeChange={(n) => {
              setSize(n);
              setPage(1);
            }}
            className="mt-4"
          />
        </Card>
      ) : null}

      <ContactFormDialog
        open={adding}
        contact={null}
        onClose={() => setAdding(false)}
        onSaved={(_contact, message) => reload(message)}
      />

      <ContactFormDialog
        open={editing !== null}
        contact={editing}
        onClose={() => setEditing(null)}
        onSaved={(_contact, message) => reload(message)}
      />

      <ContactImportDialog
        open={importing}
        onClose={() => setImporting(false)}
        onImported={(result) =>
          reload(`Import finished: ${result.created} created, ${result.updated} updated, ${result.invalid} invalid.`)
        }
      />

      <ConfirmDialog
        open={deleting !== null}
        onClose={() => {
          setDeleting(null);
          setDeleteFailure(null);
        }}
        onConfirm={() => void confirmDelete()}
        title="Delete this contact?"
        body={`${deleting?.name === "" ? deleting?.email : deleting?.name} is removed from your contacts. Any suppression of the address stays. This can't be undone.`}
        confirmLabel="Delete"
        pendingLabel="Deleting…"
        pending={deletePending}
        {...(deleteFailure !== null ? { error: deleteFailure } : {})}
      />
    </div>
  );
}

/** The screen's single polite live region — every dialog announces its result into it. */
export function ContactsPage() {
  return (
    <Announcer>
      <Contacts />
    </Announcer>
  );
}
