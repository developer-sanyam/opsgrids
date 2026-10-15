import { useState } from "react";

import { createSegment } from "@/api/client";
import type { ContactListOut, CrmStatus, SegmentOut } from "@/api/types";
import { failureMessage } from "@/lib/errors";
import { Button, Dialog, InlineAlert, Select, TextField } from "@/ui";

const CRM_STATUSES = [
  { value: "lead", label: "Lead" },
  { value: "qualified", label: "Qualified" },
  { value: "customer", label: "Customer" },
  { value: "dormant", label: "Dormant" },
];

/**
 * Save a segment: a filter over the contact book that a campaign evaluates when it sends, so a
 * contact who joins the list tomorrow is in tomorrow's send. Every field narrows; none is everyone.
 * Consent is not a field here — the campaign's channel decides it, and the service only ever sends
 * marketing to contacts with a standing opt-in for that channel.
 */
export function SegmentFormDialog({
  open,
  lists,
  onClose,
  onSaved,
}: {
  open: boolean;
  lists: ContactListOut[];
  onClose: () => void;
  onSaved: (segment: SegmentOut) => void;
}) {
  const [name, setName] = useState("");
  const [listId, setListId] = useState("");
  const [createdDays, setCreatedDays] = useState("");
  const [activeDays, setActiveDays] = useState("");
  const [crmStatus, setCrmStatus] = useState("");
  const [nameError, setNameError] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  function days(value: string): number | null {
    const n = Number.parseInt(value, 10);
    return Number.isFinite(n) && n > 0 ? n : null;
  }

  async function submit() {
    if (submitting) return;
    if (name.trim() === "") {
      setNameError("Every segment needs a name to pick it out by.");
      return;
    }
    setSubmitting(true);
    setFailure(null);
    try {
      const saved = await createSegment({
        name: name.trim(),
        list_id: listId === "" ? null : listId,
        created_within_days: days(createdDays),
        active_within_days: days(activeDays),
        crm_status: crmStatus === "" ? null : (crmStatus as CrmStatus),
      });
      setName("");
      setListId("");
      setCreatedDays("");
      setActiveDays("");
      setCrmStatus("");
      onSaved(saved);
    } catch (caught) {
      setFailure(failureMessage(caught));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog
      open={open}
      onClose={() => (submitting ? undefined : onClose())}
      title="Save a segment"
      description="A filter over your contacts, worked out again every time a campaign sends."
      size="md"
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={submitting}>
            Cancel
          </Button>
          <Button onClick={() => void submit()} disabled={submitting}>
            {submitting ? "Saving…" : "Save segment"}
          </Button>
        </>
      }
    >
      <div aria-live="polite" className="empty:hidden">
        {failure !== null ? (
          <InlineAlert tone="danger" title="That segment wasn't saved" className="mb-4">
            {failure}
          </InlineAlert>
        ) : null}
      </div>
      <form
        className="flex flex-col gap-4"
        noValidate
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <TextField
          label="Segment name"
          value={name}
          onChange={(event) => {
            setName(event.target.value);
            setNameError(null);
          }}
          maxLength={120}
          required
          {...(nameError !== null ? { error: nameError } : {})}
        />
        <Select
          label="Only contacts on this list"
          value={listId}
          onChange={(event) => setListId(event.target.value)}
          options={lists.map((list) => ({ value: list.id, label: list.name }))}
          placeholder="Any list, or none"
        />
        <TextField
          label="Added in the last … days"
          type="number"
          inputMode="numeric"
          min={1}
          value={createdDays}
          onChange={(event) => setCreatedDays(event.target.value)}
          optional
        />
        <TextField
          label="Sent a campaign email in the last … days"
          type="number"
          inputMode="numeric"
          min={1}
          value={activeDays}
          onChange={(event) => setActiveDays(event.target.value)}
          optional
        />
        <Select
          label="Only contacts with this CRM status"
          value={crmStatus}
          onChange={(event) => setCrmStatus(event.target.value)}
          options={CRM_STATUSES}
          placeholder="Any status"
        />
      </form>
    </Dialog>
  );
}
