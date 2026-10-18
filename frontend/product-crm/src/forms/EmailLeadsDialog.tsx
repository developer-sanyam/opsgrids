import { useEffect, useMemo, useRef, useState } from "react";

import {
  createLeadCampaign,
  listEmailTemplates,
  listUsableSenders,
  previewLeadEmail,
  scheduleLeadCampaign,
  sendLeadCampaign,
} from "@/api/coldEmail";
import type { LeadAudiencePreview, LeadSelection } from "@/api/coldEmail";
import { problemMessage, useAsync } from "@opsgrids/client";
import { Button, DateTimeField, Dialog, InlineAlert, Select, Skeleton, TextArea, TextField } from "@opsgrids/ui";

interface EmailLeadsDialogProps {
  selection: LeadSelection;
  /** The Leads list's own words for what is selected ("3 selected leads", "all new leads"). */
  selectionLabel: string;
  onClose: () => void;
  /** The campaign was handed to Engage: the sentence to announce. */
  onDone: (message: string) => void;
}

type When = "now" | "later";

const MERGE_HELP =
  "Merge fields: {{first_name}}, {{company_name}}, {{lead_status}}. Add a fallback after a bar, like {{first_name|there}}.";

function browserZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "Asia/Kolkata";
  } catch {
    return "Asia/Kolkata";
  }
}

function Count({ label, value, hint }: { label: string; value: number; hint?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="text-sm text-muted">{label}</dt>
      <dd className="tnum text-sm font-semibold text-ink" title={hint}>
        {value}
      </dd>
    </div>
  );
}

function Audience({ preview }: { preview: LeadAudiencePreview }) {
  const room = Math.max(preview.daily_cap - preview.sent_today, 0);
  return (
    <div className="flex flex-col gap-3 rounded-tile border border-line bg-surface-muted p-4">
      <h3 className="text-sm font-semibold text-ink">Who gets it</h3>
      <dl className="flex flex-col gap-2">
        <Count label="Leads selected" value={preview.selected} />
        <Count label="With an email address" value={preview.with_email} />
        <Count
          label="Left out: unsubscribed or suppressed"
          value={preview.excluded}
          hint="An unsubscribe, a bounce, a complaint or an opt-out always wins."
        />
        <Count label="Left out: converted or disqualified" value={preview.skipped_status} />
        <div className="mt-1 border-t border-line pt-2">
          <Count label="Will be emailed" value={preview.sendable} />
        </div>
      </dl>
      <p className="text-xs text-muted">
        Daily limit for cold email: {preview.daily_cap} a day, {preview.sent_today} sent today.
        {preview.sendable > room
          ? ` The first ${room} can go today; the rest follow on the next days.`
          : ""}
      </p>
    </div>
  );
}

/**
 * "Email these leads": B2B outreach under legitimate interest (ADR-0038 addendum). Picks or writes
 * the message, shows who it would reach and how it reads for a real lead, then creates an Engage
 * campaign for exactly those leads and sends or schedules it through the ordinary campaign
 * endpoints, so every Engage gate still applies. Every address, scope and permission check is the
 * server's; this dialog only reports what it said.
 *
 * If the draft was created but the send is refused (an unverified sender, say), the draft is kept
 * and a retry sends that same draft rather than making another.
 */
export function EmailLeadsDialog({ selection, selectionLabel, onClose, onDone }: EmailLeadsDialogProps) {
  const templates = useAsync(listEmailTemplates, []);
  const senders = useAsync(listUsableSenders, []);
  const zone = useMemo(browserZone, []);

  const [templateId, setTemplateId] = useState("");
  const [senderId, setSenderId] = useState("");
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [when, setWhen] = useState<When>("now");
  const [at, setAt] = useState<string | null>(null);
  const [fieldError, setFieldError] = useState<string | undefined>(undefined);

  const [preview, setPreview] = useState<LeadAudiencePreview | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const draftId = useRef<string | null>(null);
  const subjectRef = useRef<HTMLInputElement>(null);

  // The audience and the sample re-read as the message is typed, a beat after the last keystroke.
  useEffect(() => {
    const controller = new AbortController();
    const timer = setTimeout(() => {
      previewLeadEmail(selection, { subject, text_body: body }, controller.signal)
        .then((next) => {
          setPreview(next);
          setPreviewError(null);
        })
        .catch((error: unknown) => {
          if (!controller.signal.aborted) setPreviewError(problemMessage(error));
        });
    }, 400);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [selection, subject, body]);

  function pickTemplate(id: string) {
    setTemplateId(id);
    const chosen = templates.status === "success" ? templates.data.find((t) => t.id === id) : undefined;
    if (chosen === undefined) return;
    setSubject(chosen.subject ?? "");
    setBody(chosen.body);
  }

  const senderOptions = senders.status === "success" ? senders.data.map((s) => ({ value: s.id, label: `${s.name} <${s.address}>` })) : [];
  const unknown = preview?.unknown_fields ?? [];
  const ready =
    senderId !== "" &&
    subject.trim() !== "" &&
    body.trim() !== "" &&
    preview !== null &&
    preview.sendable > 0 &&
    unknown.length === 0 &&
    (when === "now" || at !== null);

  async function submit() {
    setFailure(null);
    setFieldError(undefined);
    if (when === "later" && (at === null || new Date(at).getTime() <= Date.now())) {
      setFieldError("Choose a time in the future.");
      return;
    }
    setPending(true);
    try {
      if (draftId.current === null) {
        const made = await createLeadCampaign(selection, {
          subject: subject.trim(),
          text_body: body,
          sender_id: senderId,
          name: `Cold email, ${selectionLabel}`.slice(0, 160),
        });
        draftId.current = made.campaign.id;
      }
      if (when === "now") await sendLeadCampaign(draftId.current);
      else if (at !== null) await scheduleLeadCampaign(draftId.current, at);
      const n = preview?.sendable ?? 0;
      onDone(
        when === "now"
          ? `Sending to ${n} ${n === 1 ? "lead" : "leads"}. Follow it under Campaigns in Engage.`
          : `Scheduled for ${n} ${n === 1 ? "lead" : "leads"}.`,
      );
    } catch (error) {
      setFailure(
        draftId.current !== null
          ? `${problemMessage(error)} The draft is saved in Engage; fix this and try again.`
          : problemMessage(error),
      );
    } finally {
      setPending(false);
    }
  }

  return (
    <Dialog
      open
      onClose={() => {
        if (!pending) onClose();
      }}
      title="Email these leads"
      description={`To ${selectionLabel}. A one-click unsubscribe link goes in every email, and anyone who has unsubscribed or opted out is left out.`}
      size="lg"
      initialFocusRef={subjectRef}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={pending}>
            Cancel
          </Button>
          <Button onClick={() => void submit()} disabled={!ready || pending}>
            {pending ? (when === "now" ? "Sending…" : "Scheduling…") : when === "now" ? "Send now" : "Schedule send"}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <div aria-live="polite" className="empty:hidden">
          {failure !== null ? (
            <InlineAlert tone="danger" title="This wasn't sent">
              {failure}
            </InlineAlert>
          ) : null}
        </div>

        {senders.status === "success" && senders.data.length === 0 ? (
          <InlineAlert tone="warning" title="No verified sender yet">
            Verify a sender in Engage first (Settings, then Senders) so the email can go out as you.
          </InlineAlert>
        ) : null}

        <div className="grid gap-4 sm:grid-cols-2">
          <Select
            label="Send as"
            value={senderId}
            onChange={(e) => setSenderId(e.target.value)}
            options={senderOptions}
            placeholder={senders.status === "loading" ? "Loading senders…" : "Choose a sender"}
            disabled={pending}
          />
          <Select
            label="Start from a template"
            value={templateId}
            onChange={(e) => pickTemplate(e.target.value)}
            options={templates.status === "success" ? templates.data.map((t) => ({ value: t.id, label: t.id })) : []}
            placeholder={templates.status === "loading" ? "Loading templates…" : "Write my own"}
            disabled={pending}
          />
        </div>

        <TextField
          ref={subjectRef}
          label="Subject"
          value={subject}
          onChange={(e) => setSubject(e.target.value)}
          required
          maxLength={255}
          disabled={pending}
        />
        <TextArea
          label="Message"
          value={body}
          onChange={(e) => setBody(e.target.value)}
          rows={7}
          required
          helper={MERGE_HELP}
          disabled={pending}
        />

        {unknown.length > 0 ? (
          <InlineAlert tone="warning" title="Some merge fields can't be filled">
            {unknown.map((n) => `{{${n}}}`).join(", ")} is not a lead field. Fix it before sending.
          </InlineAlert>
        ) : null}

        {preview === null && previewError === null ? (
          <div aria-busy="true" className="flex flex-col gap-2">
            <span className="sr-only">Counting who would get this</span>
            <Skeleton className="h-32 w-full" />
          </div>
        ) : null}
        {previewError !== null ? (
          <InlineAlert tone="danger" title="Couldn't count who would get this">
            {previewError}
          </InlineAlert>
        ) : null}
        {preview !== null ? (
          <div className="grid gap-4 md:grid-cols-2">
            <Audience preview={preview} />
            <div className="flex flex-col gap-2 rounded-tile border border-line p-4">
              <h3 className="text-sm font-semibold text-ink">
                {preview.sample ? `How it reads for ${preview.sample.full_name}` : "Preview"}
              </h3>
              {preview.sample ? (
                <>
                  <p className="break-words text-sm font-semibold text-ink">{preview.sample.subject || "No subject yet"}</p>
                  <p className="whitespace-pre-wrap break-words text-sm text-ink-soft">{preview.sample.text_body || "No message yet"}</p>
                </>
              ) : (
                <p className="text-sm text-muted">
                  {preview.sendable === 0
                    ? "None of these leads can be emailed."
                    : "Write a subject and message to see it filled in for a real lead."}
                </p>
              )}
            </div>
          </div>
        ) : null}

        <div className="grid gap-4 sm:grid-cols-2">
          <Select
            label="When"
            value={when}
            onChange={(e) => setWhen(e.target.value as When)}
            options={[
              { value: "now", label: "Send now" },
              { value: "later", label: "Schedule for later" },
            ]}
            disabled={pending}
          />
          {when === "later" ? (
            <DateTimeField
              label="Send at"
              value={at}
              onChange={setAt}
              zone={zone}
              min={new Date().toISOString()}
              helper={`Your time zone: ${zone.replace(/_/g, " ")}`}
              disabled={pending}
              {...(fieldError !== undefined ? { error: fieldError } : {})}
            />
          ) : null}
        </div>
      </div>
    </Dialog>
  );
}
