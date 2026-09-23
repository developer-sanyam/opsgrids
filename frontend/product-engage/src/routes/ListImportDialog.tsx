import { useEffect, useRef, useState } from "react";

import { addListMembers, importContacts } from "@/api/client";
import { CONTACT_IMPORT_MAX } from "@/api/types";
import type { ContactImportOut } from "@/api/types";
import { parseContactCsvRows } from "@/lib/csv";
import { failureMessage } from "@/lib/errors";
import { Button, Dialog, InlineAlert, Tag, TextArea } from "@/ui";
import type { TagTone } from "@/ui";

/**
 * Paste a spreadsheet, import every row into the organization's contacts, and add whichever rows
 * imported cleanly to this list — in that order, and reported as two separate facts rather than
 * one blended count.
 *
 * `POST /engage/contacts/import` has no notion of a list: it upserts contacts by address for the
 * whole organization, exactly as `ContactImportDialog` uses it. This dialog is what ties an
 * import to *this* list — the ids of every row that came back `created` or `updated` are the ones
 * `POST /engage/lists/{list_id}/members` is called with next. A row reported `invalid` never
 * reaches the list, because there was no contact for it to add.
 */

const RESULT_TONE: Record<ContactImportOut["results"][number]["status"], TagTone> = {
  created: "positive",
  updated: "info",
  invalid: "negative",
};

const RESULT_LABEL: Record<ContactImportOut["results"][number]["status"], string> = {
  created: "Created",
  updated: "Updated",
  invalid: "Invalid",
};

interface Outcome {
  import: ContactImportOut;
  added: number;
  /** The service's sentence when the rows imported and the add to the list was refused. */
  addFailure: string | null;
}

export function ListImportDialog({
  open,
  listId,
  onClose,
  onImported,
}: {
  open: boolean;
  listId: string;
  onClose: () => void;
  onImported: (outcome: {
    created: number;
    updated: number;
    invalid: number;
    added: number;
    /** The rows imported but the add to the list was refused, so `added` is 0. */
    addFailed: boolean;
  }) => void;
}) {
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const [lines, setLines] = useState<number[]>([]);
  const outcomeRef = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    if (outcome !== null) outcomeRef.current?.focus();
  }, [outcome]);

  function close() {
    setText("");
    setError(null);
    setFailure(null);
    setOutcome(null);
    setLines([]);
    onClose();
  }

  async function submit() {
    if (submitting) return;
    const parsed = parseContactCsvRows(text);
    if (parsed.length === 0) {
      setError("Paste at least one row — one address per line, name optional after a comma.");
      setFailure(null);
      return;
    }
    if (parsed.length > CONTACT_IMPORT_MAX) {
      setError(
        `That's ${parsed.length} rows — split the paste into batches of ${CONTACT_IMPORT_MAX} or fewer.`,
      );
      setFailure(null);
      return;
    }

    setError(null);
    setFailure(null);
    setSubmitting(true);
    try {
      let imported: ContactImportOut;
      try {
        imported = await importContacts(parsed.map(({ email, name }) => ({ email, name })));
      } catch (caught) {
        // The pasted text stays: a refused import is not a reason to make the customer find and
        // re-copy the spreadsheet.
        setFailure(failureMessage(caught));
        return;
      }

      // The two calls are two facts. The import has already happened — the contacts exist — so a
      // refused add must not read as "the import didn't run", and must not throw the outcome away.
      const contactIds = imported.results
        .map((row) => row.id)
        .filter((id): id is string => id !== null);
      let added = 0;
      let addFailure: string | null = null;
      if (contactIds.length > 0) {
        try {
          added = (await addListMembers(listId, { contact_ids: contactIds })).changed;
        } catch (caught) {
          addFailure = failureMessage(caught);
        }
      }

      setOutcome({ import: imported, added, addFailure });
      setLines(parsed.map((row) => row.line));
      onImported({
        created: imported.created,
        updated: imported.updated,
        invalid: imported.invalid,
        added,
        addFailed: addFailure !== null,
      });
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog
      open={open}
      onClose={close}
      title="Import contacts into this list"
      description={`Paste rows as email, name — one contact per line, up to ${CONTACT_IMPORT_MAX} at a time. Every row that imports cleanly is added to this list.`}
      size="lg"
      footer={
        outcome === null ? (
          <>
            <Button variant="ghost" onClick={close} disabled={submitting}>
              Cancel
            </Button>
            <Button onClick={() => void submit()} disabled={submitting}>
              {submitting ? "Importing…" : "Import and add"}
            </Button>
          </>
        ) : (
          <Button onClick={close}>Done</Button>
        )
      }
    >
      {outcome === null ? (
        <>
          <div aria-live="polite" className="empty:hidden">
            {failure !== null ? (
              <InlineAlert tone="danger" title="That import didn't run" className="mb-4">
                {failure}
              </InlineAlert>
            ) : null}
            {failure === null && error !== null ? (
              <InlineAlert tone="warning" title="Check the pasted rows" className="mb-4">
                {error}
              </InlineAlert>
            ) : null}
          </div>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              void submit();
            }}
          >
            <TextArea
              label="Contacts to import"
              value={text}
              onChange={(event) => {
                setText(event.target.value);
                if (error !== null) setError(null);
              }}
              placeholder={"email,name\npriya@vaayu-logistics.example.com,Priya Raghavan\narjun@vaayu-freight.example.com,Arjun Mehta"}
              rows={10}
              spellCheck={false}
              disabled={submitting}
              helper="Each row is reported back on its own once the import runs — created, updated or invalid."
            />
          </form>
        </>
      ) : (
        <div className="flex flex-col gap-4">
          {/* Where focus lands once the import has run: the button that was pressed has just been
              replaced by Done, and focus on the document behind a modal is focus nobody can see. */}
          <h3 ref={outcomeRef} tabIndex={-1} className="text-base font-bold text-ink outline-none">
            Import results
          </h3>
          {outcome.addFailure !== null ? (
            <InlineAlert
              tone="danger"
              title="The rows imported, but they were not added to this list"
            >
              {outcome.addFailure} They are in your contacts now; add them from this list with Add
              contacts.
            </InlineAlert>
          ) : null}
          <div aria-live="polite" className="flex flex-wrap gap-2 text-sm font-semibold text-ink">
            <span>{outcome.import.created} created</span>
            <span aria-hidden>·</span>
            <span>{outcome.import.updated} updated</span>
            <span aria-hidden>·</span>
            <span>{outcome.import.invalid} invalid</span>
            <span aria-hidden>·</span>
            <span>{outcome.added} added to this list</span>
          </div>
          <div className="max-h-80 overflow-y-auto rounded-tile border border-line">
            <table className="w-full min-w-sm border-collapse text-left text-sm">
              <caption className="sr-only">Import results, one row per pasted line</caption>
              <thead>
                <tr className="border-b border-line text-xs font-bold uppercase tracking-wider text-faint">
                  <th scope="col" className="px-3 py-2">
                    Row
                  </th>
                  <th scope="col" className="px-3 py-2">
                    Result
                  </th>
                  <th scope="col" className="px-3 py-2">
                    Detail
                  </th>
                </tr>
              </thead>
              <tbody>
                {outcome.import.results.map((row) => (
                  <tr key={row.index} className="border-t border-line align-top">
                    <td className="px-3 py-2 tabular-nums text-muted">
                      {lines[row.index] ?? row.index + 1}
                    </td>
                    <td className="px-3 py-2">
                      <Tag tone={RESULT_TONE[row.status]}>{RESULT_LABEL[row.status]}</Tag>
                    </td>
                    <td className="px-3 py-2 text-muted">{row.error ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </Dialog>
  );
}
