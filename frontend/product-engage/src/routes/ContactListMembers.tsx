import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { Can, useAsync } from "@opsgrids/client";
import { ArrowLeftIcon, FileTextIcon, UserPlusIcon, UsersIcon } from "@opsgrids/ui";

import { getList, listListMembers, removeListMembers } from "@/api/client";
import type { ContactOut } from "@/api/types";
import { failureMessage } from "@/lib/errors";
import { formatNumber } from "@/lib/format";
import {
  Announcer,
  Button,
  Card,
  ConfirmDialog,
  EmptyState,
  LoadFailure,
  PageHeader,
  Pagination,
  Skeleton,
  useAnnounce,
} from "@/ui";
import { ListImportDialog } from "./ListImportDialog";
import { ListMemberPickerDialog } from "./ListMemberPickerDialog";

/**
 * Who is on one list — addresses masked, exactly as `_contact_out` returns them, the same rule
 * `Contacts.tsx` follows. Removing a member here removes them from *this list only*: the contact
 * itself, and any other list it is on, is untouched.
 */

function MemberRow({ contact, onRemove }: { contact: ContactOut; onRemove: () => void }) {
  return (
    <tr className="border-t border-line align-top">
      <th scope="row" className="py-3 pr-4 text-left">
        <span className="block text-sm font-bold text-ink">
          {contact.name === "" ? "—" : contact.name}
        </span>
        <span className="block font-mono text-xs text-muted">{contact.email}</span>
      </th>
      <td className="py-3 whitespace-nowrap">
        <Can permission="engage.audience.list.write" scope="ORG">
          <Button variant="ghost" size="sm" className="text-negative" onClick={onRemove}>
            Remove
            <span className="sr-only"> {contact.name === "" ? contact.email : contact.name} from this list</span>
          </Button>
        </Can>
      </td>
    </tr>
  );
}

const NO_MEMBERS: ContactOut[] = [];

function ContactListMembers({ listId }: { listId: string }) {
  const announce = useAnnounce();
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(25);
  const [picking, setPicking] = useState(false);
  const [importing, setImporting] = useState(false);
  const [removing, setRemoving] = useState<ContactOut | null>(null);
  const [removeFailure, setRemoveFailure] = useState<string | null>(null);
  const [removePending, setRemovePending] = useState(false);

  const loader = useCallback(
    () => Promise.all([getList(listId), listListMembers(listId, { page, size })]),
    [listId, page, size],
  );
  const state = useAsync(loader, [listId, page, size]);

  const list = state.status === "success" ? state.data[0] : null;
  const membersPage = state.status === "success" ? state.data[1] : null;
  const members = membersPage?.items ?? NO_MEMBERS;

  useEffect(() => {
    if (state.status === "success" && members.length === 0 && page > 1) {
      setPage(page - 1);
    }
  }, [state.status, members.length, page]);

  // The import dialog is deliberately left open: what it shows next is the per-row outcome, and
  // "Done" is what closes it — the same shape `Contacts.tsx` gives `ContactImportDialog`.
  function reload(message: string) {
    announce(message);
    setPicking(false);
    state.reload();
  }

  async function confirmRemove() {
    if (removing === null || removePending) return;
    setRemovePending(true);
    setRemoveFailure(null);
    try {
      await removeListMembers(listId, { contact_ids: [removing.id] });
      const name = removing.name === "" ? removing.email : removing.name;
      setRemoving(null);
      reload(`${name} removed from this list.`);
    } catch (caught) {
      setRemoveFailure(failureMessage(caught));
    } finally {
      setRemovePending(false);
    }
  }

  return (
    <div className="rise flex flex-col gap-6">
      <Link
        to="/lists"
        className="inline-flex w-fit items-center gap-1.5 text-sm font-semibold text-muted transition-colors hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
      >
        <ArrowLeftIcon className="size-4" />
        All lists
      </Link>

      <PageHeader
        eyebrow="Audience · List"
        title={list?.name ?? "List"}
        description={
          list === null
            ? "Who is on this list."
            : list.description === ""
              ? `${formatNumber(list.member_count)} ${list.member_count === 1 ? "member" : "members"}.`
              : `${list.description} · ${formatNumber(list.member_count)} ${list.member_count === 1 ? "member" : "members"}.`
        }
        actions={
          <Can permission="engage.audience.list.write" scope="ORG">
            <div className="flex gap-2">
              <Button
                variant="outline"
                onClick={() => setImporting(true)}
                leadingIcon={<FileTextIcon className="size-4" />}
              >
                Import
              </Button>
              <Button onClick={() => setPicking(true)} leadingIcon={<UserPlusIcon className="size-4" />}>
                Add contacts
              </Button>
            </div>
          </Can>
        }
      />

      {state.status === "loading" ? (
        <Card className="flex flex-col gap-4 p-6">
          <Skeleton className="h-6 w-48" />
          <Skeleton className="h-32 w-full rounded-tile" />
        </Card>
      ) : null}

      {state.status === "error" ? (
        <LoadFailure error={state.error} title="Couldn't load this list" />
      ) : null}

      {state.status === "success" && members.length === 0 ? (
        <Card className="p-6">
          <EmptyState
            icon={<UsersIcon className="size-6" />}
            title="No members yet"
            description="Add contacts one at a time, or import a spreadsheet of up to 1,000 addresses at once."
            action={
              <Can permission="engage.audience.list.write" scope="ORG">
                <Button onClick={() => setPicking(true)}>Add contacts</Button>
              </Can>
            }
          />
        </Card>
      ) : null}

      {state.status === "success" && members.length > 0 ? (
        <Card className="p-6">
          <div className="overflow-x-auto">
            <table className="w-full min-w-lg border-collapse text-left">
              <caption className="sr-only">Contacts on this list, addresses masked</caption>
              <thead>
                <tr className="text-xs font-bold uppercase tracking-wider text-faint">
                  <th scope="col" className="pb-2 pr-4">
                    Contact
                  </th>
                  <th scope="col" className="pb-2">
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {members.map((contact) => (
                  <MemberRow
                    key={contact.id}
                    contact={contact}
                    onRemove={() => setRemoving(contact)}
                  />
                ))}
              </tbody>
            </table>
          </div>
          {membersPage !== null ? (
            <Pagination
              page={membersPage.page}
              pages={membersPage.pages}
              total={membersPage.total}
              size={membersPage.size}
              noun="members"
              onPageChange={setPage}
              onSizeChange={(n) => {
                setSize(n);
                setPage(1);
              }}
              className="mt-4"
            />
          ) : null}
        </Card>
      ) : null}

      <ListMemberPickerDialog
        open={picking}
        listId={listId}
        onClose={() => setPicking(false)}
        onAdded={(result) =>
          reload(
            result.changed === 0
              ? "Everyone selected was already on this list."
              : `${result.changed} ${result.changed === 1 ? "contact" : "contacts"} added.`,
          )
        }
      />

      <ListImportDialog
        open={importing}
        listId={listId}
        onClose={() => setImporting(false)}
        onImported={(outcome) =>
          reload(
            `Import finished: ${outcome.created} created, ${outcome.updated} updated, ${outcome.invalid} invalid, ${outcome.added} added to this list.`,
          )
        }
      />

      <ConfirmDialog
        open={removing !== null}
        onClose={() => {
          setRemoving(null);
          setRemoveFailure(null);
        }}
        onConfirm={() => void confirmRemove()}
        title="Remove from this list?"
        body={`${removing?.name === "" ? removing?.email : removing?.name} stays a contact, and stays on any other list — only their membership of this one is removed.`}
        confirmLabel="Remove"
        pendingLabel="Removing…"
        pending={removePending}
        {...(removeFailure !== null ? { error: removeFailure } : {})}
      />
    </div>
  );
}

/** The screen's single polite live region — every dialog announces its result into it. Reads the
 *  list id from the route rather than trusting a prop a caller could get wrong. */
export function ContactListMembersPage() {
  const { listId } = useParams<{ listId: string }>();
  if (listId === undefined) return null;
  return (
    <Announcer>
      <ContactListMembers listId={listId} />
    </Announcer>
  );
}
