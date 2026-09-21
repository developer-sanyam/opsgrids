import { useState } from "react";

import { importContacts } from "@/api/client";
import { CONTACT_IMPORT_MAX } from "@/api/types";
import type { ContactImportOut } from "@/api/types";
import { parseContactCsv } from "@/lib/csv";
import { failureMessage } from "@/lib/errors";
import { Button, Dialog, InlineAlert, Tag, TextArea } from "@/ui";
import type { TagTone } from "@/ui";

/**
 * Paste a spreadsheet, import up to `CONTACT_IMPORT_MAX` contacts, and see what happened to every
 * row — never a summary count alone.
 *
 * **The response never echoes a row's plaintext address back.** `ContactImportOut` is `created` /
 * `updated` / `invalid` counts plus one `ContactImportResult` per row (`index`, `status`, `id`,
 * `error`) — no `email` field anywhere in it. What this dialog shows for each row is the row
 * *number* it came in on, never the text that was pasted for it, so a screenshot of a failed
 * import carries no address either.
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

export function ContactImportDialog({
  open,
  onClose,
  onImported,
}: {
  open: boolean;
  onClose: () => void;
  onImported: (result: ContactImportOut) => void;
}) {
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<ContactImportOut | null>(null);

  function close() {
    setText("");
    setError(null);
    setFailure(null);
    setResult(null);
    onClose();
  }

  async function submit() {
    if (submitting) return;
    const rows = parseContactCsv(text);
    if (rows.length === 0) {
      setError("Paste at least one row — one address per line, name optional after a comma.");
      setFailure(null);
      return;
    }
    if (rows.length > CONTACT_IMPORT_MAX) {
      setError(
        `That's ${rows.length} rows — split the paste into batches of ${CONTACT_IMPORT_MAX} or fewer.`,
      );
      setFailure(null);
      return;
    }

    setError(null);
    setFailure(null);
    setSubmitting(true);
    try {
      const imported = await importContacts(rows);
      setResult(imported);
      onImported(imported);
    } catch (caught) {
      // The pasted text stays: a refused import (a permission gap, a plan limit) is not a reason
      // to make the customer find and re-copy the spreadsheet.
      setFailure(failureMessage(caught));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog
      open={open}
      onClose={close}
      title="Import contacts"
      description={`Paste rows as email, name — one contact per line, up to ${CONTACT_IMPORT_MAX} at a time. A header row starting with "email" is fine and is skipped.`}
      size="lg"
      footer={
        result === null ? (
          <>
            <Button variant="ghost" onClick={close} disabled={submitting}>
              Cancel
            </Button>
            <Button onClick={() => void submit()} disabled={submitting}>
              {submitting ? "Importing…" : "Import"}
            </Button>
          </>
        ) : (
          <Button onClick={close}>Done</Button>
        )
      }
    >
      {result === null ? (
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
          <div
            aria-live="polite"
            className="flex flex-wrap gap-2 text-sm font-semibold text-ink"
          >
            <span>{result.created} created</span>
            <span aria-hidden>·</span>
            <span>{result.updated} updated</span>
            <span aria-hidden>·</span>
            <span>{result.invalid} invalid</span>
          </div>
          <div className="max-h-80 overflow-y-auto rounded-tile border border-line">
            <table className="w-full min-w-[24rem] border-collapse text-left text-sm">
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
                {result.results.map((row) => (
                  <tr key={row.index} className="border-t border-line align-top">
                    <td className="px-3 py-2 tabular-nums text-muted">{row.index + 1}</td>
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
