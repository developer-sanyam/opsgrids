import { useCallback, useState } from "react";

import { useAsync, useCan } from "@opsgrids/client";

import { listWhatsAppConsent, recordWhatsAppOptIn, withdrawWhatsAppOptIn } from "@/api/client";
import type { ConsentEventOut, ContactOut, WhatsAppOptInSource } from "@/api/types";
import { failureMessage } from "@/lib/errors";
import { formatInstant } from "@/lib/format";
import { Button, ConfirmDialog, Dialog, InlineAlert, Select, Skeleton, Tag, TextArea, TextField } from "@/ui";
import type { SelectOption } from "@/ui";

/**
 * A contact's WhatsApp marketing consent: what the ledger says now, and — for someone holding the
 * contact write grant — recording an opt-in or withdrawing it. Marketing WhatsApp goes only to a
 * number whose newest event is an opt-in, so this is how a contact becomes reachable by a
 * WhatsApp campaign.
 *
 * The number is typed once, to record it; after that it is only ever shown masked, which is why
 * withdrawing asks for no number — the service opts out the one on record for this contact.
 */

const SOURCES: SelectOption[] = [
  { value: "in_person", label: "In person" },
  { value: "phone_call", label: "Phone call" },
  { value: "web_form", label: "Form" },
];

const SOURCE_LABEL: Record<string, string> = {
  in_person: "in person",
  phone_call: "on a phone call",
  web_form: "on a form",
  storefront_enquiry: "on the store's enquiry form",
  inbound_keyword: "by replying STOP or START",
  meta_user_preference: "through WhatsApp's own setting",
  admin: "recorded by staff",
  import: "by import",
  checkout: "at checkout",
  api: "through the API",
};

// Server rule (`ConsentEventIn.evidence`): 3–500 characters, never a phone number or address.
const DEFAULT_EVIDENCE: Record<WhatsAppOptInSource, string> = {
  in_person: "Told staff in person",
  phone_call: "Told staff on a phone call",
  web_form: "Filled in a form",
};

function Standing({ latest }: { latest: ConsentEventOut | undefined }) {
  if (latest === undefined) {
    return (
      <p className="text-sm text-muted">
        No WhatsApp opt-in on record. WhatsApp marketing will not be sent to this contact.
      </p>
    );
  }
  const optedIn = latest.status === "opted_in";
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center gap-2">
        <Tag tone={optedIn ? "positive" : "neutral"}>{optedIn ? "Opted in" : "Opted out"}</Tag>
        <span className="font-mono text-sm text-ink">{latest.address}</span>
      </div>
      <p className="text-sm text-muted">
        {optedIn ? "Opted in" : "Opted out"} {SOURCE_LABEL[latest.source] ?? latest.source},{" "}
        {formatInstant(latest.occurred_at)}.
        {latest.evidence ? ` “${latest.evidence}”` : ""}
      </p>
    </div>
  );
}

export function WhatsAppConsentDialog({
  contact,
  onClose,
  onChanged,
}: {
  contact: ContactOut;
  onClose: () => void;
  onChanged: (message: string) => void;
}) {
  const canWrite = useCan("engage.audience.contact.write", "ORG");
  const loader = useCallback(() => listWhatsAppConsent(contact.id), [contact.id]);
  const history = useAsync(loader, [contact.id]);
  const latest = history.status === "success" ? history.data.items[0] : undefined;

  const [phone, setPhone] = useState("");
  const [source, setSource] = useState<WhatsAppOptInSource>("in_person");
  const [note, setNote] = useState("");
  const [phoneError, setPhoneError] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [notInEffect, setNotInEffect] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [confirmingWithdraw, setConfirmingWithdraw] = useState(false);

  const who = contact.name === "" ? contact.email : contact.name;

  async function record() {
    if (submitting) return;
    if (!/^\+[1-9][\d\s\-().]{6,}$/.test(phone.trim())) {
      setPhoneError("Enter the WhatsApp number with its country code, like +91 98765 43210.");
      return;
    }
    setPhoneError(null);
    setFailure(null);
    setSubmitting(true);
    try {
      const recorded = await recordWhatsAppOptIn({
        contactId: contact.id,
        phone: phone.trim(),
        source,
        evidence: note.trim() === "" ? DEFAULT_EVIDENCE[source] : note.trim(),
      });
      setPhone("");
      setNote("");
      setNotInEffect(!recorded.effective.allowed);
      history.reload();
      onChanged(`WhatsApp opt-in recorded for ${who}.`);
    } catch (caught) {
      setFailure(failureMessage(caught));
    } finally {
      setSubmitting(false);
    }
  }

  async function withdraw() {
    if (submitting) return;
    setFailure(null);
    setSubmitting(true);
    try {
      await withdrawWhatsAppOptIn({
        contactId: contact.id,
        source: "phone_call",
        evidence: "Withdrawn by staff from the contact view",
      });
      setConfirmingWithdraw(false);
      setNotInEffect(false);
      history.reload();
      onChanged(`WhatsApp opt-in withdrawn for ${who}.`);
    } catch (caught) {
      setConfirmingWithdraw(false);
      setFailure(failureMessage(caught));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog
      open
      onClose={() => {
        if (!submitting) onClose();
      }}
      title={`WhatsApp marketing — ${who}`}
      description="Marketing on WhatsApp goes only to people who said yes. Record how they told you."
      size="sm"
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={submitting}>
            Close
          </Button>
          {canWrite ? (
            <Button onClick={() => void record()} disabled={submitting}>
              {submitting ? "Saving…" : "Record opt-in"}
            </Button>
          ) : null}
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <div aria-live="polite" className="empty:hidden">
          {failure !== null ? (
            <InlineAlert tone="danger" title="That wasn't saved">
              {failure}
            </InlineAlert>
          ) : null}
          {notInEffect ? (
            <InlineAlert tone="warning" title="Recorded, but not in effect">
              This number replied STOP earlier. It stays opted out until the customer sends START.
            </InlineAlert>
          ) : null}
        </div>

        <section aria-label="Current standing" className="flex flex-col gap-2">
          {history.status === "loading" ? <Skeleton className="h-10 w-full" /> : null}
          {history.status === "error" ? (
            <InlineAlert tone="danger" title="Couldn't load the WhatsApp history">
              {failureMessage(history.error)}
            </InlineAlert>
          ) : null}
          {history.status === "success" ? <Standing latest={latest} /> : null}
          {canWrite && latest?.status === "opted_in" ? (
            <div>
              <Button variant="outline" size="sm" onClick={() => setConfirmingWithdraw(true)} disabled={submitting}>
                Withdraw opt-in
              </Button>
            </div>
          ) : null}
        </section>

        {canWrite ? (
          <form
            noValidate
            className="flex flex-col gap-4"
            onSubmit={(event) => {
              event.preventDefault();
              void record();
            }}
          >
            <TextField
              label="WhatsApp number"
              type="tel"
              value={phone}
              onChange={(event) => {
                setPhone(event.target.value);
                if (phoneError !== null) setPhoneError(null);
              }}
              placeholder="+91 98765 43210"
              helper="With the country code."
              autoComplete="off"
              required
              disabled={submitting}
              {...(phoneError !== null ? { error: phoneError } : {})}
            />
            <Select
              label="How they told you"
              value={source}
              onChange={(event) => setSource(event.target.value as WhatsAppOptInSource)}
              options={SOURCES}
              disabled={submitting}
            />
            <TextArea
              label="Note (optional)"
              value={note}
              onChange={(event) => setNote(event.target.value)}
              helper="A reference, like “trade fair, stall 12”. Not a phone number or email address."
              maxLength={500}
              rows={2}
              disabled={submitting}
            />
          </form>
        ) : null}
      </div>

      <ConfirmDialog
        open={confirmingWithdraw}
        onClose={() => setConfirmingWithdraw(false)}
        onConfirm={() => void withdraw()}
        title="Withdraw the WhatsApp opt-in?"
        body={`${who} will get no more WhatsApp marketing until a new opt-in is recorded.`}
        confirmLabel="Withdraw"
        pendingLabel="Withdrawing…"
        pending={submitting}
      />
    </Dialog>
  );
}
