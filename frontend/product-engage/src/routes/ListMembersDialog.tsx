import { useEffect, useState } from "react";

import { addListMembers, listContacts, listListMembers, removeListMembers } from "@/api/client";
import type { ContactListOut, ContactOut } from "@/api/types";
import { failureMessage } from "@/lib/errors";
import { Button, CheckboxGroup, Dialog, InlineAlert, LoadFailure, Skeleton } from "@/ui";

/**
 * Who is on a list, as a set of checkboxes over the whole contact book.
 *
 * On submit, the chosen set is diffed against membership as it was when the dialog opened:
 * newly-checked contacts go to `addListMembers`, newly-unchecked ones go to `removeListMembers`.
 * Both are idempotent per contact on the service side, so a partial failure here (add succeeds,
 * remove fails) leaves nothing double-applied to retry.
 */
export function ListMembersDialog({
  list,
  onClose,
  onChanged,
}: {
  list: ContactListOut | null;
  onClose: () => void;
  onChanged: (message: string) => void;
}) {
  const open = list !== null;
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<Error | null>(null);
  const [contacts, setContacts] = useState<ContactOut[]>([]);
  const [initialMemberIds, setInitialMemberIds] = useState<string[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  useEffect(() => {
    if (list === null) return;
    let cancelled = false;
    setLoading(true);
    setLoadError(null);
    Promise.all([listContacts(), listListMembers(list.id)])
      .then(([allContacts, members]) => {
        if (cancelled) return;
        const memberIds = members.items.map((c) => c.id);
        setContacts(allContacts.items);
        setInitialMemberIds(memberIds);
        setSelected(memberIds);
        setLoading(false);
      })
      .catch((caught: unknown) => {
        if (cancelled) return;
        setLoadError(caught instanceof Error ? caught : new Error(String(caught)));
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [list]);

  function close() {
    setFailure(null);
    onClose();
  }

  async function submit() {
    if (list === null || submitting) return;
    const toAdd = selected.filter((id) => !initialMemberIds.includes(id));
    const toRemove = initialMemberIds.filter((id) => !selected.includes(id));
    if (toAdd.length === 0 && toRemove.length === 0) {
      close();
      return;
    }

    setSubmitting(true);
    setFailure(null);
    try {
      if (toAdd.length > 0) await addListMembers(list.id, { contact_ids: toAdd });
      if (toRemove.length > 0) await removeListMembers(list.id, { contact_ids: toRemove });
      const parts: string[] = [];
      if (toAdd.length > 0) parts.push(`${toAdd.length} added`);
      if (toRemove.length > 0) parts.push(`${toRemove.length} removed`);
      onChanged(`${list.name}: ${parts.join(", ")}.`);
    } catch (caught) {
      setFailure(failureMessage(caught));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog
      open={open}
      onClose={close}
      title={list === null ? "Members" : `Members of ${list.name}`}
      description="Every contact in the audience — check who belongs on this list."
      size="lg"
      footer={
        <>
          <Button variant="ghost" onClick={close} disabled={submitting}>
            Cancel
          </Button>
          <Button onClick={() => void submit()} disabled={submitting || loading || loadError !== null}>
            {submitting ? "Saving…" : "Save membership"}
          </Button>
        </>
      }
    >
      {loading ? (
        <div className="flex flex-col gap-3">
          <Skeleton className="h-5 w-full" />
          <Skeleton className="h-5 w-full" />
          <Skeleton className="h-5 w-full" />
        </div>
      ) : null}

      {loadError !== null ? (
        <LoadFailure error={loadError} title="Couldn't load contacts for this list" />
      ) : null}

      {!loading && loadError === null && contacts.length === 0 ? (
        <InlineAlert tone="info" title="No contacts yet">
          Add someone to the contact book first.
        </InlineAlert>
      ) : null}

      {!loading && loadError === null && contacts.length > 0 ? (
        <div className="flex flex-col gap-4">
          {failure !== null ? (
            <InlineAlert tone="danger" title="Membership wasn't fully saved">
              {failure}
            </InlineAlert>
          ) : null}
          <CheckboxGroup
            label="Contacts"
            value={selected}
            onChange={setSelected}
            options={contacts.map((c) => ({
              value: c.id,
              label: c.name === "" ? c.email : `${c.name} — ${c.email}`,
              disabled: submitting,
            }))}
          />
        </div>
      ) : null}
    </Dialog>
  );
}
