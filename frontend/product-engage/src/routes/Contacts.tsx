import { useCallback, useState } from "react";

import { useAsync } from "@opsgrids/client";
import { FileTextIcon, UserPlusIcon, UsersIcon } from "@opsgrids/ui";

import { deleteContact, listContacts } from "@/api/client";
import type { ContactOut } from "@/api/types";
import { failureMessage } from "@/lib/errors";
import { formatInstant } from "@/lib/format";
import { useDebouncedValue } from "@/lib/useDebouncedValue";
import {
  Announcer,
  Button,
  Card,
  ConfirmDialog,
  EmptyState,
  LoadFailure,
  PageHeader,
  Pagination,
  SearchInput,
  Skeleton,
  useAnnounce,
} from "@/ui";
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

function ContactRow({
  contact,
  onEdit,
  onDelete,
}: {
  contact: ContactOut;
  onEdit: () => void;
  onDelete: () => void;
}) {
  return (
    <tr className="border-t border-line align-top">
      <th scope="row" className="py-3 pr-4 text-left">
        <span className="block text-sm font-bold text-ink">
          {contact.name === "" ? "—" : contact.name}
        </span>
        <span className="block font-mono text-xs text-muted">{contact.email}</span>
      </th>
      <td className="py-3 pr-4 text-sm text-muted">{formatInstant(contact.created_at)}</td>
      <td className="py-3 pr-4 text-sm text-muted">{formatInstant(contact.updated_at)}</td>
      <td className="py-3 whitespace-nowrap">
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={onEdit}>
            Edit
            <span className="sr-only"> {contact.name === "" ? contact.email : contact.name}</span>
          </Button>
          <Button variant="ghost" size="sm" className="text-negative" onClick={onDelete}>
            Delete
            <span className="sr-only"> {contact.name === "" ? contact.email : contact.name}</span>
          </Button>
        </div>
      </td>
    </tr>
  );
}

const NO_CONTACTS: ContactOut[] = [];

function Contacts() {
  const announce = useAnnounce();
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

  const loader = useCallback(
    () => listContacts({ search: debouncedSearch, page, size }),
    [debouncedSearch, page, size],
  );
  const state = useAsync(loader, [debouncedSearch, page, size]);
  const contacts = state.status === "success" ? state.data.items : NO_CONTACTS;

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

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Audience"
        title="Contacts"
        description="Who Engage may reach. Addresses are sealed at rest and only ever shown masked, exactly as the service returns them."
        actions={
          <div className="flex gap-2">
            <Button
              variant="outline"
              onClick={() => setImporting(true)}
              leadingIcon={<FileTextIcon className="size-4" />}
            >
              Import
            </Button>
            <Button onClick={() => setAdding(true)} leadingIcon={<UserPlusIcon className="size-4" />}>
              Add a contact
            </Button>
          </div>
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
            title={search.trim() === "" ? "No contacts yet" : "No contacts match that search"}
            description={
              search.trim() === ""
                ? "Add a contact by hand, or import a spreadsheet of up to 1,000 at once."
                : `Nothing matches “${search.trim()}”. Clear the search to see everyone.`
            }
          />
        </Card>
      ) : null}

      {state.status === "success" && contacts.length > 0 ? (
        <Card className="p-6">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[48rem] border-collapse text-left">
              <caption className="sr-only">Contacts, their email mask and when each changed</caption>
              <thead>
                <tr className="text-xs font-bold uppercase tracking-wider text-faint">
                  <th scope="col" className="pb-2 pr-4">
                    Contact
                  </th>
                  <th scope="col" className="pb-2 pr-4">
                    Added
                  </th>
                  <th scope="col" className="pb-2 pr-4">
                    Updated
                  </th>
                  <th scope="col" className="pb-2">
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {contacts.map((contact) => (
                  <ContactRow
                    key={contact.id}
                    contact={contact}
                    onEdit={() => setEditing(contact)}
                    onDelete={() => setDeleting(contact)}
                  />
                ))}
              </tbody>
            </table>
          </div>
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
