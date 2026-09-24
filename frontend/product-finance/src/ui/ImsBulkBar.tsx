import { useState } from "react";

import { decideImsDocumentsBulk } from "@/api/client";
import type { ImsAction, ImsDocument } from "@/api/types";
import { actionLabel, bulkConsequence, needsReason } from "@/data/compliance";
import { ApiError, Can, problemMessage, useMutation } from "@opsgrids/client";
import { Button, ConfirmDialog, TextArea } from "@opsgrids/ui";

/** The four things a person can decide, in the order the queue is worked. */
const ACTIONS: readonly ImsAction[] = ["accept", "reject", "pending_action", "no_action"];

interface ImsBulkBarProps {
  /** The rows ticked on this page — never rows on another page or hidden by a filter. */
  selected: readonly ImsDocument[];
  /** Each row's own reason, by document id. What a person typed beside the row wins. */
  reasons: Readonly<Record<string, string>>;
  /** Whose name goes against each decision in fixture mode; live, the token says. */
  decidedBy: string;
  onClear: () => void;
  onRecorded: (result: { count: number; action: ImsAction }) => void;
}

/** A row's reason: what was typed beside it, else the shared one, else nothing (`null`). */
function noteFor(
  doc: ImsDocument,
  reasons: Readonly<Record<string, string>>,
  shared: string,
): string | null {
  const own = (reasons[doc.id] ?? "").trim();
  if (own !== "") return own;
  const fallback = shared.trim();
  return fallback !== "" ? fallback : null;
}

/**
 * What acts on the ticked rows: four decisions, and the confirmation each one goes through.
 *
 * **It is not a shortcut around deciding.** Every ticked row becomes its own append-only decision
 * with its own reason, its own audit row and this person's name against it — the request is
 * atomic, so all of them are recorded or none, but each is exactly the decision it would have been
 * alone. The dialog says so, and says what the action does to this organization's money before it
 * happens, because "are you sure?" is not a consequence.
 *
 * **Reasons are per row, with a fallback.** A reject or a hold with no reason is a row nobody can
 * explain in a year, so those two need one — the row's own, or the shared one beside the buttons —
 * and the message names the rows still missing one rather than saying "a reason is required". It
 * is checked when the button is pressed, before any dialog: a validation that lived inside the
 * confirmation would ask for the same click twice.
 *
 * **Nothing here sends anything to GSTN.** Recording queues each decision; sending is the push
 * control's, behind its own permission.
 *
 * The buttons sit behind `<Can>` on the decide permission: that hides a control and protects
 * nothing, the endpoint refuses on its own.
 */
export function ImsBulkBar({ selected, reasons, decidedBy, onClear, onRecorded }: ImsBulkBarProps) {
  const [pending, setPending] = useState<ImsAction | null>(null);
  const [shared, setShared] = useState("");
  const [missing, setMissing] = useState<string | null>(null);

  const record = useMutation(
    (action: ImsAction) =>
      decideImsDocumentsBulk(
        selected.map((doc) => ({
          document_id: doc.id,
          action,
          note: noteFor(doc, reasons, shared),
        })),
        decidedBy,
      ),
    {
      onSuccess: (_results, action) => {
        setPending(null);
        setShared("");
        setMissing(null);
        onRecorded({ count: selected.length, action });
      },
    },
  );

  function choose(action: ImsAction) {
    record.reset();
    if (needsReason(action)) {
      const lacking = selected.filter((doc) => noteFor(doc, reasons, shared) === null);
      if (lacking.length > 0) {
        setMissing(
          `${actionLabel(action)} needs a reason for every document. Still missing one for: ${lacking
            .map((doc) => doc.document_number)
            .join(", ")}.`,
        );
        return;
      }
    }
    setMissing(null);
    setPending(action);
  }

  const count = selected.length;
  const noun = `${count} document${count === 1 ? "" : "s"}`;
  const serverError = record.error
    ? record.error instanceof ApiError && record.error.status === 404
      ? "One of the selected documents is no longer part of this organization. Nothing was recorded."
      : problemMessage(record.error)
    : undefined;

  return (
    <div
      role="region"
      aria-label="Decide the selected documents"
      className="flex flex-col gap-3 rounded-tile bg-accent-soft/60 p-3 sm:p-4"
    >
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <p className="text-sm font-semibold text-ink" aria-live="polite">
          {noun} selected
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <Can
            permission="finance.compliance.ims.decide"
            scope="ORG"
            fallback={
              <span className="text-sm text-muted">
                Deciding needs <span className="money">finance.compliance.ims.decide</span>.
              </span>
            }
          >
            {ACTIONS.map((action) => (
              <Button
                key={action}
                size="sm"
                variant={action === "accept" ? "primary" : "outline"}
                onClick={() => choose(action)}
              >
                {actionLabel(action)}…
              </Button>
            ))}
          </Can>
          <Button size="sm" variant="ghost" onClick={onClear}>
            Clear selection
          </Button>
        </div>
      </div>

      <TextArea
        label="Reason for every row without its own"
        rows={2}
        maxLength={1000}
        optional
        value={shared}
        onChange={(event) => setShared(event.target.value)}
        helper="A row's own reason wins; this covers the rest. Reject and hold need one."
        {...(missing !== null ? { error: missing } : {})}
      />

      <ConfirmDialog
        open={pending !== null}
        onClose={() => {
          setPending(null);
          record.reset();
        }}
        onConfirm={() => {
          if (pending !== null) void record.mutate(pending);
        }}
        tone={pending === "reject" ? "danger" : "primary"}
        title={pending === null ? "Confirm" : `${actionLabel(pending)} ${noun}?`}
        body={
          pending === null
            ? ""
            : `${bulkConsequence(pending)} Each is recorded as its own decision, with your name and its own reason. Nothing is sent to GSTN until someone with the push permission sends the period.`
        }
        confirmLabel={pending === null ? "Confirm" : actionLabel(pending)}
        pendingLabel="Recording…"
        pending={record.pending}
        {...(serverError !== undefined ? { error: serverError } : {})}
        {...(record.error instanceof ApiError && record.error.problem?.request_id
          ? { reference: record.error.problem.request_id }
          : {})}
      />
    </div>
  );
}
