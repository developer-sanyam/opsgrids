import { useCallback, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { useAsync } from "@opsgrids/client";
import { FileTextIcon, MinusCircleIcon, UserPlusIcon, UsersIcon } from "@opsgrids/ui";

import { deleteContact, listContacts } from "@/api/client";
import type { ContactOut } from "@/api/types";
import { failureMessage } from "@/lib/errors";
import { formatInstant } from "@/lib/format";
import {
  Announcer,
  Button,
  Card,
  ConfirmDialog,
  EmptyState,
  InlineAlert,
  LoadFailure,
  PageHeader,
  SearchInput,
  Skeleton,
  useAnnounce,
} from "@/ui";
import { ContactFormDialog } from "./ContactFormDialog";
import { ContactImportDialog } from "./ContactImportDialog";

/**
 * The contact book: everyone Engage knows about, searchable by email or name.
 *
 * `?q=` is the sidebar's search, same wiring as Domains — it is sent to the server rather than
 * filtered here, so what the customer sees is exactly what a campaign's audience query would
 * match, not a client-side approximation of it.
 */

const NO_CONTACTS: ContactOut[] = [];

function ContactRow({
  contact,
  onDelete,
}: {
  contact: ContactOut;
  onDelete: () => void;
}) {
  return (
    <tr className="border-t border-line align-top">
      <th scope="row" className="py-3 pr-4 text-left">
        <span className="block font-mono text-sm break-all text-ink">{contact.email}</span>
      </th>
      <td className="py-3 pr-4 text-sm text-ink">{contact.name === "" ? "—" : contact.name}</td>
      <td className="py-3 pr-4 text-sm text-muted">{formatInstant(contact.created_at)}</td>
      <td className="py-3 whitespace-nowrap">
        <Button
          variant="danger"
          size="sm"
          onClick={onDelete}
          leadingIcon={<MinusCircleIcon className="size-4" />}
        >
          Remove
          <span className="sr-only"> {contact.email}</span>
        </Button>
      </td>
    </tr>
  );
}

function Contacts() {
  const announce = useAnnounce();
  const [params, setParams] = useSearchParams();
  const q = params.get("q") ?? "";
  const [attempt, setAttempt] = useState(0);
  const [adding, setAdding] = useState(false);
  const [importing, setImporting] = useState(false);
  const [removing, setRemoving] = useState<ContactOut | null>(null);
  const [removeError, setRemoveError] = useState<string | null>(null);
  const [removePending, setRemovePending] = useState(false);

  const loader = useCallback(() => listContacts(q === "" ? {} : { q }), [q]);
  const state = useAsync(loader, [attempt, q]);
  const contacts = state.status === "success" ? state.data.items : NO_CONTACTS;

  function reload(message: string) {
    announce(message);
    setAdding(false);
    setImporting(false);
    setAttempt((current) => current + 1);
  }

  async function confirmRemove() {
    if (removing === null || removePending) return;
    setRemovePending(true);
    setRemoveError(null);
    try {
      await deleteContact(removing.id);
      const email = removing.email;
      setRemoving(null);
      reload(`${email} removed.`);
    } catch (caught) {
      setRemoveError(failureMessage(caught));
    } finally {
      setRemovePending(false);
    }
  }

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Audience"
        title="Contacts"
        description="Everyone Engage knows about. A campaign's audience is built from this list, its lists, and who has been suppressed."
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

      <SearchInput
        label="Search contacts by email or name"
        placeholder="Search contacts"
        value={q}
        onChange={(event) => {
          const next = event.target.value;
          setParams(next === "" ? {} : { q: next }, { replace: true });
        }}
      />

      {state.status === "loading" ? (
        <Card className="flex flex-col gap-4 p-6">
          <Skeleton className="h-6 w-48" />
          <Skeleton className="h-32 w-full rounded-tile" />
        </Card>
      ) : null}

      {state.status === "error" ? (
        <LoadFailure error={state.error} title="Couldn't load your contacts" />
      ) : null}

      {state.status === "success" && contacts.length === 0 && q === "" ? (
        <Card className="p-6">
          <EmptyState
            icon={<UsersIcon className="size-6" />}
            title="No contacts yet"
            description="Add a contact one at a time, or paste a list to import — both are above."
          />
        </Card>
      ) : null}

      {state.status === "success" && contacts.length === 0 && q !== "" ? (
        <InlineAlert tone="info" live="off" title={`No contacts match “${q}”`}>
          Try a different email or name, or clear the search.
        </InlineAlert>
      ) : null}

      {contacts.length > 0 ? (
        <Card className="p-6">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[40rem] border-collapse text-left">
              <caption className="sr-only">Contacts, their name and when they were added</caption>
              <thead>
                <tr className="text-xs font-bold uppercase tracking-wider text-faint">
                  <th scope="col" className="pb-2 pr-4">
                    Email
                  </th>
                  <th scope="col" className="pb-2 pr-4">
                    Name
                  </th>
                  <th scope="col" className="pb-2 pr-4">
                    Added
                  </th>
                  <th scope="col" className="pb-2">
                    <span className="sr-only">Remove</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {contacts.map((contact) => (
                  <ContactRow
                    key={contact.id}
                    contact={contact}
                    onDelete={() => {
                      setRemoveError(null);
                      setRemoving(contact);
                    }}
                  />
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      ) : null}

      <ContactFormDialog
        open={adding}
        onClose={() => setAdding(false)}
        onSaved={(_contact, message) => reload(message)}
      />

      <ContactImportDialog
        open={importing}
        onClose={() => setImporting(false)}
        onImported={(report) => {
          // The dialog stays open to show its own per-row report; only the contact book
          // behind it needs to catch up, so this refetches without closing the dialog the
          // way `reload` does for every other write on this screen.
          announce(
            `${report.created} added, ${report.updated} updated` +
              (report.errors > 0 ? `, ${report.errors} failed` : "") +
              ".",
          );
          setAttempt((current) => current + 1);
        }}
      />

      <ConfirmDialog
        open={removing !== null}
        onClose={() => {
          setRemoving(null);
          setRemoveError(null);
        }}
        onConfirm={() => void confirmRemove()}
        title="Remove this contact?"
        body={
          <>
            <strong>{removing?.email}</strong> will no longer appear in the contact book or in
            any list it belongs to. This does not undo a suppression already recorded against it.
          </>
        }
        confirmLabel="Remove"
        pendingLabel="Removing…"
        pending={removePending}
        {...(removeError !== null ? { error: removeError } : {})}
      />
    </div>
  );
}

export function ContactsPage() {
  return (
    <Announcer>
      <Contacts />
    </Announcer>
  );
}
