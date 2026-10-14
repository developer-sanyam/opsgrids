import { useState } from "react";

import { logActivity } from "@/api/client";
import type { Activity, ActivityDirection, ActivityType, Company, Contact } from "@/api/types";
import { maybe } from "@/lib/optional";
import { ApiError, problemMessage, useMutation } from "@opsgrids/client";
import {
  DateTimeField,
  FormDialog,
  InlineAlert,
  Select,
  TextArea,
  TextField,
  useFormDirty,
} from "@opsgrids/ui";

const TYPE_OPTIONS: { value: ActivityType; label: string }[] = [
  { value: "call", label: "Call" },
  { value: "email", label: "Email" },
  { value: "meeting", label: "Meeting" },
  { value: "note", label: "Note" },
  { value: "task", label: "Task" },
];

const DIRECTION_OPTIONS: { value: ActivityDirection; label: string }[] = [
  { value: "outbound", label: "Outbound" },
  { value: "inbound", label: "Inbound" },
];

/** The wall clock every relative timestamp in this app already reads in (`lib/format.ts`). */
const ZONE = "Asia/Kolkata";

interface FixedContext {
  dealId?: string;
  companyId?: string;
  contactId?: string;
  /** What to show beside "Logging against" — the deal name, the company name. */
  label: string;
}

interface ActivityFormDialogProps {
  /** Pass a fixed context from a deal or company screen — the target is shown, not editable.
   *  Omit it (the Activities screen) to let the caller pick a company and, optionally, a
   *  contact within it. */
  context?: FixedContext;
  companies?: Company[];
  contacts?: Contact[];
  onClose: () => void;
  onSaved: (activity: Activity) => void;
}

/**
 * Log a call, email, meeting, note or task — `POST /deals/activities`. Append-only: there is no
 * edit or delete affordance anywhere this renders from, matching the timeline it writes to.
 */
export function ActivityFormDialog({
  context,
  companies = [],
  contacts = [],
  onClose,
  onSaved,
}: ActivityFormDialogProps) {
  const [type, setType] = useState<ActivityType>("call");
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [direction, setDirection] = useState<ActivityDirection>("outbound");
  const [occurredAt, setOccurredAt] = useState<string | null>(null);
  const [companyId, setCompanyId] = useState(context?.companyId ?? "");
  const [contactId, setContactId] = useState(context?.contactId ?? "");

  const fields = { type, subject, body, direction, occurredAt, companyId, contactId };
  const [initial] = useState(fields);
  const dirty = useFormDirty(initial, fields);

  const [subjectError, setSubjectError] = useState<string | undefined>(undefined);
  const [companyError, setCompanyError] = useState<string | undefined>(undefined);
  const [occurredAtError, setOccurredAtError] = useState<string | undefined>(undefined);

  const hasDirection = type === "call" || type === "email";
  const pickTarget = context === undefined;
  const contactOptions = contacts.filter((c) => c.company_id === companyId);

  const save = useMutation(
    () =>
      logActivity({
        type,
        subject: subject.trim(),
        ...(body.trim() ? { body: body.trim() } : {}),
        ...(context?.dealId ? { deal_id: context.dealId } : {}),
        ...(context?.companyId ? { company_id: context.companyId } : {}),
        ...(!context && companyId ? { company_id: companyId } : {}),
        ...(context?.contactId ? { contact_id: context.contactId } : {}),
        ...(!context && contactId ? { contact_id: contactId } : {}),
        ...(hasDirection ? { direction } : {}),
        ...(occurredAt ? { occurred_at: occurredAt } : {}),
      }),
    { onSuccess: (saved) => onSaved(saved) },
  );

  function submit() {
    setSubjectError(undefined);
    setCompanyError(undefined);
    setOccurredAtError(undefined);

    if (subject.trim().length < 1) {
      setSubjectError("Say what this touch was about.");
      return;
    }
    if (pickTarget && companyId === "") {
      setCompanyError("Choose a company to log this against.");
      return;
    }
    if (occurredAt && Date.parse(occurredAt) > Date.now()) {
      setOccurredAtError("This can't be in the future.");
      return;
    }
    void save.mutate(undefined);
  }

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title="Log activity"
      submitLabel="Log activity"
      pendingLabel="Logging…"
      pending={save.pending}
      dirty={dirty}
      {...(save.error && Object.keys(save.fieldErrors).length === 0
        ? {
            error: problemMessage(save.error),
            ...maybe("reference", (save.error as ApiError).problem?.request_id),
          }
        : {})}
    >
      {context ? (
        <InlineAlert tone="info" live="off" title={`Logging against ${context.label}`} />
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Select
            label="Company"
            value={companyId}
            onChange={(e) => {
              setCompanyId(e.target.value);
              setContactId("");
            }}
            placeholder="Choose a company"
            options={companies.map((c) => ({ value: c.id, label: c.name }))}
            required
            {...maybe("error", companyError ?? save.fieldErrors.company_id)}
          />
          <Select
            label="Contact"
            value={contactId}
            onChange={(e) => setContactId(e.target.value)}
            placeholder={companyId === "" ? "Choose a company first" : "None"}
            disabled={companyId === ""}
            options={contactOptions.map((c) => ({ value: c.id, label: c.full_name }))}
            {...maybe("error", save.fieldErrors.contact_id)}
          />
        </div>
      )}

      <Select
        label="Type"
        value={type}
        onChange={(e) => setType(e.target.value as ActivityType)}
        options={TYPE_OPTIONS}
        {...maybe("error", save.fieldErrors.type)}
      />

      {hasDirection ? (
        <Select
          label="Direction"
          value={direction}
          onChange={(e) => setDirection(e.target.value as ActivityDirection)}
          options={DIRECTION_OPTIONS}
          {...maybe("error", save.fieldErrors.direction)}
        />
      ) : null}

      <TextField
        label="Subject"
        value={subject}
        onChange={(e) => setSubject(e.target.value)}
        placeholder="Discovery call on renewal scope"
        required
        {...maybe("error", subjectError ?? save.fieldErrors.subject)}
      />

      <TextArea
        label="Notes"
        value={body}
        onChange={(e) => setBody(e.target.value)}
        optional
        rows={3}
        {...maybe("error", save.fieldErrors.body)}
      />

      <DateTimeField
        label="When"
        value={occurredAt}
        onChange={setOccurredAt}
        zone={ZONE}
        helper={`Wall clock in ${ZONE}. Leave blank to log it as now.`}
        {...maybe("error", occurredAtError ?? save.fieldErrors.occurred_at)}
      />
    </FormDialog>
  );
}
