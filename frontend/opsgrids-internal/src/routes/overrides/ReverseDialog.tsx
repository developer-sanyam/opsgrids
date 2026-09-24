import { useRef, useState } from "react";

import { useMutation } from "@opsgrids/client";
import { FormDialog, TextArea } from "@opsgrids/ui";

import { postOverride } from "@/api/client";
import type { CreditLedgerEntry, OverrideResult } from "@/api/types";
import { referenceOf } from "@/lib/errors";
import { Money } from "@/ui";
import { failureText } from "./text";

interface ReverseDialogProps {
  entry: CreditLedgerEntry;
  onClose: () => void;
  onDone: (result: OverrideResult) => void;
}

/**
 * Take a grant back. The credit ledger is append-only (CLAUDE.md), so this never edits or deletes
 * the entry — it writes a reversing row of the opposite amount, and the original stays on the
 * ledger, unchanged, with the reversal beside it.
 */
export function ReverseDialog({ entry, onClose, onDone }: ReverseDialogProps) {
  const [reason, setReason] = useState("");
  const [reasonError, setReasonError] = useState<string | undefined>(undefined);
  const reasonRef = useRef<HTMLTextAreaElement>(null);

  const run = useMutation(
    (trimmed: string) =>
      postOverride({
        kind: "comp_grant",
        organization_id: entry.organization_id,
        amount_minor: -entry.amount_minor,
        currency: entry.currency,
        is_comp: entry.is_comp,
        reason: `Reversal of ${entry.entry_id} — ${trimmed}`,
        valid_until: null,
      }),
    {
      onSuccess: (result) => {
        onClose();
        onDone(result);
      },
    },
  );

  function submit() {
    setReasonError(undefined);
    if (reason.trim().length === 0) {
      setReasonError("Give a reason — it goes into the audit log.");
      reasonRef.current?.focus();
      return;
    }
    void run.mutate(reason.trim());
  }

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title="Reverse grant"
      description="Writes a reversing entry. The original grant stays on the ledger, unchanged."
      submitLabel="Reverse grant"
      pendingLabel="Reversing…"
      submitTone="danger"
      pending={run.pending}
      initialFocusRef={reasonRef}
      error={failureText(run.error, run.fieldErrors, ["reason"])}
      reference={referenceOf(run.error)}
    >
      <p className="text-sm text-muted">
        Reversing <Money minor={entry.amount_minor} currency={entry.currency} className="font-semibold text-ink" />{" "}
        granted to <span className="font-semibold text-ink">{entry.organization_name}</span> — “{entry.reason}”.
      </p>
      <TextArea
        ref={reasonRef}
        label="Reason (required)"
        rows={2}
        value={reason}
        onChange={(event) => setReason(event.target.value)}
        placeholder="e.g. Payment recovered"
        error={reasonError ?? run.fieldErrors.reason}
      />
    </FormDialog>
  );
}
