import { useState } from "react";

import { importContacts } from "@/api/client";
import type { ContactBulkImportOut, ContactBulkRowIn } from "@/api/types";
import { failureMessage } from "@/lib/errors";
import { Button, Dialog, InlineAlert, Tag, TextArea } from "@/ui";

/**
 * Paste many contacts at once, one `email[,name]` per line.
 *
 * There is no file picker: a `<textarea>` a customer pastes a spreadsheet column into needs no
 * parser beyond splitting on commas and newlines, and it is the one shape that works identically
 * whether the source was Excel, Sheets or a CRM export. `services.bulk_import_contacts` is
 * **idempotent on email** — a row already in the audience updates instead of duplicating — so
 * pasting the same list twice after a partial failure costs nothing.
 *
 * The report is rendered exactly as the service returns it: one line per row, in the order
 * submitted, so "which line of what I pasted failed" never has to be re-derived from a diff.
 */

const MAX_ROWS = 2000;

function parseRows(text: string): ContactBulkRowIn[] {
  return text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line !== "")
    .map((line) => {
      const [email, ...rest] = line.split(",");
      const name = rest.join(",").trim();
      return name === "" ? { email: (email ?? "").trim() } : { email: (email ?? "").trim(), name };
    });
}

export function ContactImportDialog({
  open,
  onClose,
  onImported,
}: {
  open: boolean;
  onClose: () => void;
  /** Fired once the report is in hand, whether or not every row succeeded — the caller reloads
   *  the contact book either way, since `created` and `updated` rows both changed it. */
  onImported: (report: ContactBulkImportOut) => void;
}) {
  const [text, setText] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [report, setReport] = useState<ContactBulkImportOut | null>(null);

  function close() {
    setText("");
    setFailure(null);
    setReport(null);
    onClose();
  }

  const rows = parseRows(text);
  const tooMany = rows.length > MAX_ROWS;

  async function submit() {
    if (submitting || rows.length === 0 || tooMany) return;
    setSubmitting(true);
    setFailure(null);
    try {
      const result = await importContacts({ rows });
      setReport(result);
      onImported(result);
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
      title="Import contacts"
      description="One contact per line: email, or email,name. Importing an address already in the audience updates that contact instead of duplicating it."
      size="lg"
      footer={
        <>
          <Button variant="ghost" onClick={close} disabled={submitting}>
            {report === null ? "Cancel" : "Close"}
          </Button>
          {report === null ? (
            <Button
              onClick={() => void submit()}
              disabled={submitting || rows.length === 0 || tooMany}
            >
              {submitting ? "Importing…" : `Import ${rows.length || ""} contact${rows.length === 1 ? "" : "s"}`}
            </Button>
          ) : null}
        </>
      }
    >
      {report === null ? (
        <div className="flex flex-col gap-4">
          <div aria-live="polite" className="empty:hidden">
            {failure !== null ? (
              <InlineAlert tone="danger" title="That import didn't run">
                {failure}
              </InlineAlert>
            ) : null}
            {tooMany ? (
              <InlineAlert tone="warning" title="Too many rows">
                An import accepts at most {MAX_ROWS} rows at once; this has {rows.length}. Split
                it into smaller batches.
              </InlineAlert>
            ) : null}
          </div>
          <TextArea
            label="Contacts"
            value={text}
            onChange={(event) => setText(event.target.value)}
            placeholder={"jane@customer.example.com,Jane Doe\narjun@customer.example.com"}
            rows={10}
            disabled={submitting}
            helper={`${rows.length} row${rows.length === 1 ? "" : "s"} recognised.`}
          />
        </div>
      ) : (
        <div className="flex flex-col gap-4">
          <div className="flex flex-wrap gap-2">
            <Tag tone="positive">{`${report.created} created`}</Tag>
            <Tag tone="info">{`${report.updated} updated`}</Tag>
            {report.errors > 0 ? <Tag tone="negative">{`${report.errors} failed`}</Tag> : null}
          </div>
          <div className="max-h-72 overflow-y-auto rounded-tile border border-line">
            <table className="w-full border-collapse text-left text-sm">
              <caption className="sr-only">Per-row import result</caption>
              <thead>
                <tr className="border-b border-line text-xs font-bold uppercase tracking-wider text-faint">
                  <th scope="col" className="px-3 py-2">
                    Row
                  </th>
                  <th scope="col" className="px-3 py-2">
                    Email
                  </th>
                  <th scope="col" className="px-3 py-2">
                    Result
                  </th>
                </tr>
              </thead>
              <tbody>
                {report.rows.map((row) => (
                  <tr key={row.row} className="border-t border-line">
                    <td className="px-3 py-2 text-muted">{row.row + 1}</td>
                    <td className="px-3 py-2 font-mono text-xs break-all">{row.email}</td>
                    <td className="px-3 py-2">
                      {row.status === "created" ? <Tag tone="positive">Created</Tag> : null}
                      {row.status === "updated" ? <Tag tone="info">Updated</Tag> : null}
                      {row.status === "error" ? (
                        <span className="flex flex-col gap-0.5">
                          <Tag tone="negative">Failed</Tag>
                          <span className="text-xs text-muted">{row.error}</span>
                        </span>
                      ) : null}
                    </td>
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
