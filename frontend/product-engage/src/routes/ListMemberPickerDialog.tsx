import { useState } from "react";

import { CloseIcon } from "@opsgrids/ui";

import { addListMembers, listContacts } from "@/api/client";
import type { ListMembersOut } from "@/api/types";
import { failureMessage } from "@/lib/errors";
import { Button, Combobox, Dialog, InlineAlert } from "@/ui";
import type { ComboboxOption } from "@/ui";

/**
 * Add existing contacts to a list, one at a time, staged and submitted together.
 *
 * `Combobox` never filters client-side — every keystroke asks `GET /engage/contacts?search=`,
 * which is the only place "does this match" is decided, because addresses are sealed at rest and
 * this screen never holds one it could match against itself. A contact already staged is dropped
 * from the next search's candidates by id, not hidden by a client-side filter over a list this
 * screen does not otherwise keep.
 */

const CAP = 20;

export function ListMemberPickerDialog({
  open,
  listId,
  onClose,
  onAdded,
}: {
  open: boolean;
  listId: string;
  onClose: () => void;
  onAdded: (result: ListMembersOut) => void;
}) {
  const [selected, setSelected] = useState<ComboboxOption | null>(null);
  const [staged, setStaged] = useState<ComboboxOption[]>([]);
  const [failure, setFailure] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  function close() {
    setSelected(null);
    setStaged([]);
    setFailure(null);
    onClose();
  }

  function stage(option: ComboboxOption | null) {
    setSelected(null);
    if (option === null) return;
    setStaged((current) => (current.some((c) => c.id === option.id) ? current : [...current, option]));
  }

  function unstage(id: string) {
    setStaged((current) => current.filter((c) => c.id !== id));
  }

  async function loadOptions(query: string, signal: AbortSignal): Promise<ComboboxOption[]> {
    const page = await listContacts({ search: query, size: CAP });
    if (signal.aborted) return [];
    const stagedIds = new Set(staged.map((c) => c.id));
    return page.items
      .filter((contact) => !stagedIds.has(contact.id))
      .map((contact) => ({
        id: contact.id,
        label: contact.name === "" ? contact.email : contact.name,
        sublabel: contact.name === "" ? undefined : contact.email,
      }));
  }

  async function submit() {
    if (submitting || staged.length === 0) return;
    setSubmitting(true);
    setFailure(null);
    try {
      const result = await addListMembers(listId, { contact_ids: staged.map((c) => c.id) });
      setStaged([]);
      onAdded(result);
    } catch (caught) {
      // The staged chips stay: a refused add (a permission gap, a plan limit) is not a reason to
      // make the customer search for the same five people again.
      setFailure(failureMessage(caught));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog
      open={open}
      onClose={close}
      title="Add contacts to this list"
      description="Search by name or an exact email address, and add as many as you need before submitting."
      size="lg"
      footer={
        <>
          <Button variant="ghost" onClick={close} disabled={submitting}>
            Cancel
          </Button>
          <Button onClick={() => void submit()} disabled={submitting || staged.length === 0}>
            {submitting
              ? "Adding…"
              : staged.length === 0
                ? "Add contacts"
                : `Add ${staged.length} ${staged.length === 1 ? "contact" : "contacts"}`}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <div aria-live="polite" className="empty:hidden">
          {failure !== null ? (
            <InlineAlert tone="danger" title="Those contacts weren't added">
              {failure}
            </InlineAlert>
          ) : null}
        </div>

        <Combobox
          label="Find a contact"
          value={selected}
          onChange={stage}
          loadOptions={loadOptions}
          placeholder="Search by name or paste an exact email address"
          helper="Selecting a result stages it below rather than closing the dialog."
          cap={CAP}
          disabled={submitting}
          copy={{
            idle: "Start typing a name or an exact email address.",
            noMatches: (query) => `No contact matches “${query}”.`,
            noMatchesHint: "Check the spelling, or add them from Contacts first.",
          }}
        />

        {staged.length > 0 ? (
          <div className="flex flex-col gap-2">
            <span className="text-xs font-bold uppercase tracking-wider text-faint">
              Ready to add ({staged.length})
            </span>
            <ul className="flex flex-wrap gap-2">
              {staged.map((contact) => (
                <li key={contact.id}>
                  <span className="inline-flex items-center gap-2 rounded-full bg-accent-soft py-1 pl-3 pr-1 text-sm font-semibold text-accent-ink">
                    <span className="truncate">{contact.label}</span>
                    <button
                      type="button"
                      onClick={() => unstage(contact.id)}
                      disabled={submitting}
                      aria-label={`Remove ${contact.label} from what's staged to add`}
                      className="grid size-6 place-items-center rounded-full transition-colors hover:bg-surface/60"
                    >
                      <CloseIcon className="size-4" />
                    </button>
                  </span>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </div>
    </Dialog>
  );
}
