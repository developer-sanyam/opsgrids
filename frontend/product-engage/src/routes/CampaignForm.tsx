import { useEffect, useMemo, useRef, useState } from "react";

import { ApiError, Can, fieldErrorMap, usePermissionStatus } from "@opsgrids/client";
import { SendIcon } from "@opsgrids/ui";

import {
  countSegment,
  createCampaign,
  editCampaign,
  listSegments,
  listWhatsAppTemplates,
} from "@/api/client";
import type {
  AbMetric,
  CampaignAudience,
  CampaignChannel,
  CampaignIn,
  CampaignOut,
  CampaignPatch,
  ProviderTemplateOut,
  SegmentOut,
  SenderOut,
} from "@/api/types";
import { formatNumber } from "@/lib/format";
import { failureMessage } from "@/lib/errors";
import { failureText, partialNotice } from "@/lib/refs";
import type { Refs, ResolvedLists } from "@/lib/refs";
import type { GateField } from "@/lib/sendGate";
import { Button, Card, FormSection, InlineAlert, RadioGroup, Select, TextArea, TextField } from "@/ui";
import type { SelectOption } from "@/ui";
import { CampaignPreview } from "./CampaignPreview";
import { SegmentFormDialog } from "./SegmentFormDialog";
import { TestSendDialog } from "./TestSendDialog";

/**
 * Draft or edit a campaign. Nothing on this form sends anything: saving writes a draft, and
 * sending is a separate, confirmed action on the panel beside it — `POST .../send` acts on the
 * *saved* draft, which is why this reports its unsaved state upward and the send control refuses
 * to fire while it is set.
 *
 * Validation mirrors the service's and never replaces it. The rules copied here are the ones a
 * customer can fix without a round trip (a name, a sender, a list when the audience is one, the
 * length caps). A line break in a subject is the service's rule too, but a single-line input cannot
 * hold one, so there is nothing to mirror. Everything else — a sender that no longer exists, a list
 * that was deleted, a subject the service still refuses — comes back as a `422` and is mapped onto
 * the control it names when it names one (`fieldErrorMap`), or shown at the top when it does not.
 *
 * **Only senders on verified domains are offered**, the same rule `SenderFormDialog` applies to
 * domains: the service refuses to *send* as anyone else, so offering one is offering a choice that
 * ends in a refusal. A sender that is untested is offered and says so in words — a draft may be
 * written before the test send is done; the send gate is what insists on it. A draft whose current
 * sender has since lost its domain keeps that sender selected and says why it cannot send, rather
 * than showing an empty select that claims nothing was ever chosen.
 *
 * The test-send button here is the *sender's* test (`POST /engage/senders/{id}/test`), the one on
 * Senders — the service has no per-campaign test send, and this does not pretend to. It verifies
 * the address can send; it does not preview this campaign in a mailbox.
 */

interface Values {
  name: string;
  channel: CampaignChannel;
  senderId: string;
  templateId: string;
  templateValues: Record<string, string>;
  audience: CampaignAudience;
  listId: string;
  segmentId: string;
  subject: string;
  htmlBody: string;
  textBody: string;
  abTest: boolean;
  subjectB: string;
  htmlBodyB: string;
  textBodyB: string;
  /** Kept as typed, so a half-typed number is not coerced under the person's cursor. */
  abSplitPercent: string;
  abMetric: AbMetric;
  abWaitHours: string;
}

type Errors = Partial<Record<keyof Values, string>>;

const NAME_MAX = 200;
const SUBJECT_MAX = 255;
const BODY_MAX = 500_000;

/** The wire field names a `problem.errors[]` entry can carry, and the control each belongs to. */
const WIRE_FIELDS: Record<string, keyof Values> = {
  name: "name",
  sender_id: "senderId",
  provider_template_id: "templateId",
  audience: "audience",
  list_id: "listId",
  segment_id: "segmentId",
  subject: "subject",
  html_body: "htmlBody",
  text_body: "textBody",
  subject_b: "subjectB",
  html_body_b: "htmlBodyB",
  text_body_b: "textBodyB",
  ab_split_percent: "abSplitPercent",
  ab_metric: "abMetric",
  ab_wait_minutes: "abWaitHours",
};

const VERSION_OPTIONS = [
  { value: "one", label: "One version", description: "Everyone gets the subject and body above." },
  {
    value: "ab",
    label: "A/B test two versions",
    description:
      "A share of the audience gets each version; after the wait, the one with the better open or click rate goes to everyone else.",
  },
];

const METRIC_OPTIONS = [
  { value: "open", label: "Open rate" },
  { value: "click", label: "Click rate" },
];

const AUDIENCE_OPTIONS = [
  {
    value: "all_sendable",
    label: "Everyone you may mail",
    description:
      "Every contact except addresses on your Suppressions list — unsubscribed, bounced, complained and blocked addresses are always skipped.",
  },
  {
    value: "list",
    label: "One list",
    description: "Only the contacts on a list you choose. Suppressed addresses are still skipped.",
  },
  {
    value: "segment",
    label: "A segment",
    description:
      "A saved filter, worked out again when the campaign sends. Only contacts who opted in to this channel are reached.",
  },
];

const CHANNEL_OPTIONS = [
  { value: "email", label: "Email", description: "Your own subject and body, sent as one of your senders." },
  {
    value: "whatsapp",
    label: "WhatsApp",
    description:
      "One of your Meta-approved templates, sent only to contacts whose WhatsApp number opted in to marketing messages.",
  },
];

/** An email template's subject and plain-text body, to start a new campaign from. */
export interface CampaignPrefill {
  subject: string;
  textBody: string;
}

function seed(campaign: CampaignOut | null, prefill?: CampaignPrefill): Values {
  return {
    name: campaign?.name ?? "",
    channel: campaign?.channel ?? "email",
    senderId: campaign?.sender_id ?? "",
    templateId: campaign?.provider_template_id ?? "",
    templateValues: campaign?.template_values ?? {},
    audience: campaign?.audience ?? "all_sendable",
    listId: campaign?.list_id ?? "",
    segmentId: campaign?.segment_id ?? "",
    subject: campaign?.subject ?? prefill?.subject ?? "",
    htmlBody: campaign?.html_body ?? "",
    textBody: campaign?.text_body ?? prefill?.textBody ?? "",
    abTest: campaign?.ab_test ?? false,
    subjectB: campaign?.subject_b ?? "",
    htmlBodyB: campaign?.html_body_b ?? "",
    textBodyB: campaign?.text_body_b ?? "",
    abSplitPercent: String(campaign?.ab_split_percent ?? 10),
    abMetric: campaign?.ab_metric ?? "open",
    abWaitHours: String((campaign?.ab_wait_minutes ?? 240) / 60),
  };
}

/** The A/B fields as the service takes them, or nothing for a single-version campaign — plus the
 *  tracking the chosen metric reads, which the send gate otherwise refuses. */
function abFields(values: Values): Partial<CampaignIn> {
  if (!values.abTest) return {};
  return {
    ab_test: true,
    subject_b: values.subjectB,
    html_body_b: values.htmlBodyB,
    text_body_b: values.textBodyB,
    ab_split_percent: Number(values.abSplitPercent),
    ab_metric: values.abMetric,
    ab_wait_minutes: Math.round(Number(values.abWaitHours) * 60),
    ...(values.abMetric === "open" ? { track_opens: true } : { track_clicks: true }),
  };
}

function validate(values: Values): Errors {
  const errors: Errors = {};
  if (values.name.trim() === "") errors.name = "Give the campaign a name so you can find it later.";
  else if (values.name.trim().length > NAME_MAX) errors.name = `A name can be at most ${NAME_MAX} characters.`;
  if (values.channel === "email" && values.senderId === "") {
    errors.senderId = "Choose the sender this campaign goes out as.";
  }
  if (values.channel === "whatsapp" && values.templateId === "") {
    errors.templateId = "Choose the approved template this campaign sends.";
  }
  if (values.audience === "list" && values.listId === "") {
    errors.listId = "Choose which list this campaign goes to.";
  }
  if (values.audience === "segment" && values.segmentId === "") {
    errors.segmentId = "Choose which segment this campaign goes to.";
  }
  if (values.subject.length > SUBJECT_MAX) {
    errors.subject = `A subject can be at most ${SUBJECT_MAX} characters.`;
  }
  if (values.htmlBody.length > BODY_MAX) errors.htmlBody = "The HTML body is too long to send.";
  if (values.textBody.length > BODY_MAX) errors.textBody = "The plain-text body is too long to send.";
  if (values.abTest) {
    const split = Number(values.abSplitPercent);
    if (!Number.isInteger(split) || split < 1 || split > 50) {
      errors.abSplitPercent = "Test each version on 1 to 50 per cent of the audience.";
    }
    const minutes = Number(values.abWaitHours) * 60;
    if (!Number.isFinite(minutes) || minutes < 15 || minutes > 10080) {
      errors.abWaitHours = "Wait between a quarter of an hour and 168 hours (a week).";
    }
    if (values.subjectB.length > SUBJECT_MAX) {
      errors.subjectB = `A subject can be at most ${SUBJECT_MAX} characters.`;
    }
    if (values.htmlBodyB.length > BODY_MAX) errors.htmlBodyB = "The HTML body is too long to send.";
    if (values.textBodyB.length > BODY_MAX) errors.textBodyB = "The plain-text body is too long to send.";
  }
  return errors;
}

function toCreate(values: Values): CampaignIn {
  return {
    name: values.name.trim(),
    // Email is the service's default channel, so an email draft's body is what it always was.
    ...(values.channel === "email"
      ? {
          sender_id: values.senderId,
          subject: values.subject,
          html_body: values.htmlBody,
          text_body: values.textBody,
        }
      : {
          channel: "whatsapp",
          provider_template_id: values.templateId,
          template_values: values.templateValues,
        }),
    audience: values.audience,
    ...(values.audience === "list" ? { list_id: values.listId } : {}),
    ...abFields(values),
    ...(values.audience === "segment" ? { segment_id: values.segmentId } : {}),
  };
}

function sameValues(a: Record<string, string>, b: Record<string, string>): boolean {
  const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
  return [...keys].every((key) => (a[key] ?? "") === (b[key] ?? ""));
}

/** Only what differs from the saved draft. `audience` and `list_id` travel together because the
 *  service validates the pair, not either alone: switching to everyone omits `list_id` and the
 *  service clears it. */
function toPatch(values: Values, saved: CampaignOut): CampaignPatch {
  return {
    ...(values.name.trim() === saved.name ? {} : { name: values.name.trim() }),
    ...(values.channel === "email" && values.senderId !== saved.sender_id
      ? { sender_id: values.senderId }
      : {}),
    ...(values.channel === "whatsapp" && values.templateId !== (saved.provider_template_id ?? "")
      ? { provider_template_id: values.templateId }
      : {}),
    ...(values.channel === "whatsapp" && !sameValues(values.templateValues, saved.template_values)
      ? { template_values: values.templateValues }
      : {}),
    ...(values.subject === saved.subject ? {} : { subject: values.subject }),
    ...(values.htmlBody === saved.html_body ? {} : { html_body: values.htmlBody }),
    ...(values.textBody === saved.text_body ? {} : { text_body: values.textBody }),
    ...abPatch(values, saved),
    ...(values.audience === saved.audience &&
    (values.audience === "list" ? values.listId : null) === saved.list_id &&
    (values.audience === "segment" ? values.segmentId : null) === saved.segment_id
      ? {}
      : {
          audience: values.audience,
          ...(values.audience === "list" ? { list_id: values.listId } : {}),
          ...(values.audience === "segment" ? { segment_id: values.segmentId } : {}),
        }),
  };
}

/** The A/B fields that differ from the saved draft; turning the test off sends just that. */
function abPatch(values: Values, saved: CampaignOut): CampaignPatch {
  if (!values.abTest) return saved.ab_test === true ? { ab_test: false } : {};
  const wanted = abFields(values) as Record<string, unknown>;
  const held = saved as unknown as Record<string, unknown>;
  return Object.fromEntries(
    Object.entries(wanted).filter(([key, value]) => held[key] !== value),
  ) as CampaignPatch;
}

function isDirty(values: Values, saved: CampaignOut | null): boolean {
  if (saved === null) return true;
  return Object.keys(toPatch(values, saved)).length > 0;
}

export interface FocusRequest {
  field: GateField;
  /** Bumped on every request, so asking for the same field twice moves focus twice. */
  nonce: number;
}

export function CampaignForm({
  campaign,
  refs,
  resolved,
  focusRequest,
  prefill,
  onDirtyChange,
  onSaved,
}: {
  /** `null` to draft a new campaign; a draft to edit it. */
  campaign: CampaignOut | null;
  refs: Refs;
  /** The lists this screen can name: the paged read, plus the campaign's own by id. */
  resolved: ResolvedLists;
  focusRequest: FocusRequest | null;
  /** Fills a new campaign's subject and body from an email template. */
  prefill?: CampaignPrefill;
  onDirtyChange: (dirty: boolean) => void;
  onSaved: (campaign: CampaignOut, message: string) => void;
}) {
  const [values, setValues] = useState<Values>(() => seed(campaign, prefill));
  const [errors, setErrors] = useState<Errors>({});
  const [submitting, setSubmitting] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [testing, setTesting] = useState<SenderOut | null>(null);
  /* Senders a test send has rewritten since the page loaded — the route answers with the whole
     sender, so the option turns over on the provider's answer without a second read. */
  const [tested, setTested] = useState<Record<string, SenderOut>>({});
  const [testNotice, setTestNotice] = useState<string | null>(null);
  const [segments, setSegments] = useState<SegmentOut[]>([]);
  const [templates, setTemplates] = useState<ProviderTemplateOut[]>([]);
  const [creatingSegment, setCreatingSegment] = useState(false);
  /* Who a campaign on this channel would reach in the chosen segment right now — `null` while
     unknown (no segment chosen, still counting, or the count could not be read). */
  const [reach, setReach] = useState<number | null>(null);

  // A refused read leaves its picker empty; the service's own 422 names a missing choice.
  useEffect(() => {
    listSegments().then((page) => setSegments(page.items), () => setSegments([]));
    listWhatsAppTemplates().then(
      (page) => setTemplates(page.items.filter((t) => t.channel === "whatsapp")),
      () => setTemplates([]),
    );
  }, []);

  useEffect(() => {
    setReach(null);
    if (values.audience !== "segment" || values.segmentId === "") return;
    let live = true;
    countSegment(values.segmentId, values.channel).then(
      (out) => {
        if (live) setReach(out.recipients);
      },
      () => undefined,
    );
    return () => {
      live = false;
    };
  }, [values.audience, values.segmentId, values.channel]);

  const senderRef = useRef<HTMLSelectElement>(null);
  const listRef = useRef<HTMLSelectElement>(null);
  const subjectRef = useRef<HTMLInputElement>(null);
  const bodyRef = useRef<HTMLTextAreaElement>(null);

  // A save hands back the campaign as the service now holds it; reseed from it so "unsaved" means
  // what it says. Done during render, keyed on the prop's identity, rather than in an effect: an
  // effect also fires on mount, where the state was just initialised from the same object, and
  // would overwrite a keystroke that landed between commit and the effect flush. The parent holds
  // one stable object per fetch, so this only runs when the service has actually answered.
  const [seen, setSeen] = useState(campaign);
  if (campaign !== seen) {
    setSeen(campaign);
    setValues(seed(campaign, prefill));
    setErrors({});
    setFailure(null);
  }

  /* A caller who may read this campaign and not write it sees the draft, not a form. Known-denied
     only: while grants load, or if they could not be read, the fields stay live and the service
     refuses the save — a form that flickers read-only on every load is worse than one that
     refuses late. `readOnly` for text (still focusable and selectable), `disabled` for controls
     that have no read-only state. */
  const readOnly = usePermissionStatus("engage.campaign.campaign.write", "ORG") === "denied";
  const locked = submitting || readOnly;

  /* Grants can resolve after the form is on screen: the fields are live until the caller is known
     to lack the write grant (see above), so someone may already have typed. Once they are known
     read-only, what they typed is text no one can save, and leaving it in a field that now claims
     to be the saved draft is a lie about the draft — so it is dropped, and the form shows what the
     service holds. Done during render, keyed on the transition, like the reseed above. */
  const [wasReadOnly, setWasReadOnly] = useState(readOnly);
  if (readOnly !== wasReadOnly) {
    setWasReadOnly(readOnly);
    if (readOnly) {
      setValues(seed(campaign, prefill));
      setErrors({});
      setFailure(null);
    }
  }

  /* A member who can send but not write cannot have changed anything, so nothing here may report
     unsaved changes — that is what disables Send. */
  const dirty = !readOnly && isDirty(values, campaign);
  useEffect(() => {
    onDirtyChange(dirty);
  }, [dirty, onDirtyChange]);

  useEffect(() => {
    if (focusRequest === null) return;
    const target = {
      subject: subjectRef,
      body: bodyRef,
      sender: senderRef,
      list: listRef,
    }[focusRequest.field];
    target.current?.focus();
  }, [focusRequest]);

  const knownSenders = useMemo(
    () => refs.senders.items.map((sender) => tested[sender.id] ?? sender),
    [refs.senders.items, tested],
  );
  const domainsById = useMemo(
    () => new Map(refs.domains.items.map((domain) => [domain.id, domain])),
    [refs.domains.items],
  );
  /* Hidden only when the sender's domain is *known* not to be verified. A domain the read did not
     reach, or could not read at all, is unknown — and an unknown is offered, because the service
     refuses at send time and hiding a sender on a guess would leave a campaign unaddressable. */
  const offered = useMemo(
    () =>
      knownSenders.filter((sender) => {
        if (sender.id === campaign?.sender_id) return true;
        const domain = domainsById.get(sender.domain_id);
        return domain === undefined || domain.status === "verified";
      }),
    [knownSenders, domainsById, campaign?.sender_id],
  );
  const chosen = knownSenders.find((sender) => sender.id === values.senderId) ?? null;
  const chosenDomain = chosen === null ? undefined : domainsById.get(chosen.domain_id);
  const chosenDomainLapsed = chosenDomain !== undefined && chosenDomain.status !== "verified";

  /* A controlled select whose value is not among its options silently shows some other option. The
     current choice is therefore always an option — named if it could be, neutral if not. */
  const senderOptions: SelectOption[] = offered.map((sender) => ({
    value: sender.id,
    label: `${sender.name} <${sender.address}>${sender.usable ? "" : " — not tested yet"}`,
  }));
  if (values.senderId !== "" && !senderOptions.some((o) => o.value === values.senderId)) {
    senderOptions.push({ value: values.senderId, label: "The sender chosen for this campaign" });
  }
  const listOptions: SelectOption[] = resolved.lists.map((list) => ({
    value: list.id,
    label: `${list.name} (${list.member_count})`,
  }));
  if (values.listId !== "" && !listOptions.some((o) => o.value === values.listId)) {
    listOptions.push({
      value: values.listId,
      label: resolved.gone.has(values.listId)
        ? "A list that no longer exists"
        : "The list chosen for this campaign",
    });
  }

  const segmentOptions: SelectOption[] = segments.map((segment) => ({
    value: segment.id,
    label: segment.name,
  }));
  if (values.segmentId !== "" && !segmentOptions.some((o) => o.value === values.segmentId)) {
    segmentOptions.push({ value: values.segmentId, label: "The segment chosen for this campaign" });
  }
  const chosenTemplate = templates.find((t) => t.id === values.templateId) ?? null;
  const templateOptions: SelectOption[] = templates
    .filter((t) => t.sendable || t.id === values.templateId)
    .map((t) => ({ value: t.id, label: `${t.provider_name} (${t.language})` }));
  if (values.templateId !== "" && !templateOptions.some((o) => o.value === values.templateId)) {
    templateOptions.push({ value: values.templateId, label: "The template chosen for this campaign" });
  }

  const sendersNotice = partialNotice(refs.senders, "senders");
  const listsNotice = partialNotice(refs.lists, "lists");

  function set<K extends keyof Values>(key: K, value: Values[K]) {
    setValues((current) => ({ ...current, [key]: value }));
    setErrors((current) => {
      if (current[key] === undefined) return current;
      const next = { ...current };
      delete next[key];
      return next;
    });
  }

  async function submit() {
    if (submitting) return;
    const found = validate(values);
    setErrors(found);
    if (Object.keys(found).length > 0) {
      setFailure(null);
      return;
    }
    if (campaign !== null && !dirty) return;

    setSubmitting(true);
    setFailure(null);
    try {
      const saved =
        campaign === null
          ? await createCampaign(toCreate(values))
          : await editCampaign(campaign.id, toPatch(values, campaign));
      onSaved(saved, campaign === null ? `${saved.name} drafted.` : `${saved.name} saved.`);
    } catch (caught) {
      // Everything typed stays exactly where it is: a refused draft is usually one field away
      // from right, and a 500-thousand-character body is not something to ask a person to retype.
      const mapped: Errors = {};
      if (caught instanceof ApiError) {
        for (const [wire, message] of Object.entries(fieldErrorMap(caught.problem))) {
          const key = WIRE_FIELDS[wire];
          if (key !== undefined) mapped[key] = message;
        }
      }
      if (Object.keys(mapped).length > 0) {
        setErrors(mapped);
        setFailure(null);
      } else {
        setFailure(failureMessage(caught));
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-5">
      <Card className="p-6 lg:col-span-3">
        <h2 className="mb-4 text-base font-bold text-ink">Write the campaign</h2>

        <div aria-live="polite" className="empty:hidden">
          {failure !== null ? (
            <InlineAlert tone="danger" title="That campaign wasn't saved" className="mb-4">
              {failure}
            </InlineAlert>
          ) : null}
          {failure === null && Object.keys(errors).length > 0 ? (
            <InlineAlert tone="warning" title="Check the fields marked below" className="mb-4">
              Nothing has been sent — this is only the draft.
            </InlineAlert>
          ) : null}
        </div>

        {readOnly ? (
          <InlineAlert tone="info" live="off" title="You can read this draft, not change it" className="mb-4">
            Editing has its own permission, “Draft, edit and delete campaigns”. Ask an Engage
            administrator to grant it.
          </InlineAlert>
        ) : null}

        <form
          className="flex flex-col gap-6"
          noValidate
          onSubmit={(event) => {
            event.preventDefault();
            void submit();
          }}
        >
          <FormSection title="Campaign details" headingLevel={3} columns={1}>
          <TextField
            label="Campaign name"
            value={values.name}
            onChange={(event) => set("name", event.target.value)}
            placeholder="October delivery network update"
            helper="For you and your team — recipients never see it."
            maxLength={NAME_MAX}
            required
            readOnly={locked}
            {...(errors.name !== undefined ? { error: errors.name } : {})}
          />

          {/* The channel is fixed once drafted: an email draft and a WhatsApp draft hold different
              fields, and the service does not convert one into the other. */}
          {campaign === null ? (
            <RadioGroup
              legend="Channel"
              name="campaign-channel"
              value={values.channel}
              onChange={(value) => set("channel", value as CampaignChannel)}
              options={CHANNEL_OPTIONS.map((option) => ({ ...option, disabled: locked }))}
            />
          ) : null}

          {values.channel === "whatsapp" ? (
            <div className="flex flex-col gap-4">
              <Select
                label="Template"
                value={values.templateId}
                onChange={(event) => set("templateId", event.target.value)}
                options={templateOptions}
                placeholder={templateOptions.length === 0 ? "No approved templates yet" : "Choose a template"}
                helper="Only templates Meta has approved can be sent."
                disabled={locked}
                required
                {...(errors.templateId !== undefined ? { error: errors.templateId } : {})}
              />
              {(chosenTemplate?.variables ?? []).map((variable, index) => (
                <TextField
                  key={variable.key}
                  label={`Template value {{${index + 1}}} (${variable.key})`}
                  value={values.templateValues[variable.key] ?? ""}
                  onChange={(event) =>
                    set("templateValues", { ...values.templateValues, [variable.key]: event.target.value })
                  }
                  helper="The same for every recipient."
                  maxLength={1024}
                  readOnly={locked}
                />
              ))}
            </div>
          ) : (
          <div className="flex flex-col gap-2">
            {refs.senders.error !== null ? (
              <InlineAlert tone="warning" live="off" title="Couldn't load your senders">
                {failureText(refs.senders.error)} The sender this campaign already has is kept.
              </InlineAlert>
            ) : null}
            <Select
              ref={senderRef}
              label="Send as"
              value={values.senderId}
              onChange={(event) => set("senderId", event.target.value)}
              options={senderOptions}
              placeholder="Choose a sender"
              helper={
                chosenDomainLapsed
                  ? "The domain behind this sender is no longer verified, so it cannot send. Re-verify the domain or choose another sender."
                  : `Only senders on verified domains are offered. A sender must have passed its test send before a campaign can go out as it.${sendersNotice === null ? "" : ` ${sendersNotice}`}`
              }
              disabled={locked}
              required
              {...(errors.senderId !== undefined ? { error: errors.senderId } : {})}
            />
            {chosen !== null ? (
              <Can permission="engage.setup.sender.write" scope="ORG">
                <div className="flex flex-col items-start gap-1">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => setTesting(chosen)}
                    disabled={submitting}
                    leadingIcon={<SendIcon className="size-4" />}
                  >
                    Send a test message
                    <span className="sr-only"> as {chosen.address}</span>
                  </Button>
                  <span className="text-xs text-muted">
                    Sends Engage&rsquo;s fixed test message as this sender. It proves the sender can
                    send; it does not preview this campaign.
                  </span>
                </div>
              </Can>
            ) : null}
            <div aria-live="polite" className="empty:hidden">
              {testNotice !== null ? (
                <p className="text-sm font-semibold text-positive">{testNotice}</p>
              ) : null}
            </div>
          </div>
          )}

          <RadioGroup
            legend="Who receives it"
            name="campaign-audience"
            value={values.audience}
            onChange={(value) => set("audience", value as CampaignAudience)}
            options={AUDIENCE_OPTIONS.map((option) => ({ ...option, disabled: locked }))}
          />

          {values.audience === "list" ? (
            <div className="flex flex-col gap-2">
              {refs.lists.error !== null ? (
                <InlineAlert tone="warning" live="off" title="Couldn't load your lists">
                  {failureText(refs.lists.error)} The list this campaign already has is kept.
                </InlineAlert>
              ) : null}
              <Select
                ref={listRef}
                label="List"
                value={values.listId}
                onChange={(event) => set("listId", event.target.value)}
                options={listOptions}
                placeholder={listOptions.length === 0 ? "No lists yet" : "Choose a list"}
                helper={
                  listOptions.length === 0 && refs.lists.error === null
                    ? "Create a list under Audience → Lists first, or send to everyone you may mail."
                    : `The number is how many contacts are on it now.${listsNotice === null ? "" : ` ${listsNotice} The list this campaign already uses is always included.`}`
                }
                disabled={locked || listOptions.length === 0}
                required
                {...(errors.listId !== undefined ? { error: errors.listId } : {})}
              />
            </div>
          ) : null}

          {values.audience === "segment" ? (
            <div className="flex flex-col items-start gap-2">
              <Select
                label="Segment"
                value={values.segmentId}
                onChange={(event) => set("segmentId", event.target.value)}
                options={segmentOptions}
                placeholder={segmentOptions.length === 0 ? "No segments yet" : "Choose a segment"}
                disabled={locked}
                required
                {...(errors.segmentId !== undefined ? { error: errors.segmentId } : {})}
              />
              <p aria-live="polite" className="text-sm text-muted">
                {reach === null
                  ? ""
                  : `${formatNumber(reach)} ${reach === 1 ? "contact" : "contacts"} would receive this right now.`}
              </p>
              <Can permission="engage.audience.list.write" scope="ORG">
                <Button variant="outline" size="sm" onClick={() => setCreatingSegment(true)} disabled={locked}>
                  New segment
                </Button>
              </Can>
            </div>
          ) : null}

          {values.channel === "email" ? (
          <>
          <TextField
            ref={subjectRef}
            label="Subject"
            value={values.subject}
            onChange={(event) => set("subject", event.target.value)}
            placeholder="What's changing on your route this month"
            helper="Required before it can be sent. One line."
            maxLength={SUBJECT_MAX}
            readOnly={locked}
            {...(errors.subject !== undefined ? { error: errors.subject } : {})}
          />

          <TextArea
            ref={bodyRef}
            label="HTML body"
            value={values.htmlBody}
            onChange={(event) => set("htmlBody", event.target.value)}
            rows={10}
            spellCheck={false}
            optional
            helper="Sent exactly as written. Write at least one of the HTML and plain-text bodies."
            readOnly={locked}
            {...(errors.htmlBody !== undefined ? { error: errors.htmlBody } : {})}
          />

          <TextArea
            label="Plain-text body"
            value={values.textBody}
            onChange={(event) => set("textBody", event.target.value)}
            rows={6}
            optional
            helper="What mail apps that don't show HTML display. Worth writing even when the HTML is the one you care about."
            readOnly={locked}
            {...(errors.textBody !== undefined ? { error: errors.textBody } : {})}
          />

          <RadioGroup
            legend="Versions"
            name="campaign-versions"
            value={values.abTest ? "ab" : "one"}
            onChange={(value) => set("abTest", value === "ab")}
            options={VERSION_OPTIONS.map((option) => ({ ...option, disabled: locked }))}
          />

          {values.abTest ? (
            <>
              <TextField
                label="Version B subject"
                value={values.subjectB}
                onChange={(event) => set("subjectB", event.target.value)}
                helper="Required before it can be sent."
                maxLength={SUBJECT_MAX}
                readOnly={locked}
                {...(errors.subjectB !== undefined ? { error: errors.subjectB } : {})}
              />
              <TextArea
                label="Version B HTML body"
                value={values.htmlBodyB}
                onChange={(event) => set("htmlBodyB", event.target.value)}
                rows={8}
                spellCheck={false}
                optional
                readOnly={locked}
                {...(errors.htmlBodyB !== undefined ? { error: errors.htmlBodyB } : {})}
              />
              <TextArea
                label="Version B plain-text body"
                value={values.textBodyB}
                onChange={(event) => set("textBodyB", event.target.value)}
                rows={5}
                optional
                helper="Write at least one of version B's HTML and plain-text bodies."
                readOnly={locked}
                {...(errors.textBodyB !== undefined ? { error: errors.textBodyB } : {})}
              />
              <TextField
                label="Test group size (% of the audience for each version)"
                type="number"
                inputMode="numeric"
                min={1}
                max={50}
                value={values.abSplitPercent}
                onChange={(event) => set("abSplitPercent", event.target.value)}
                helper="10 sends version A to 10% and version B to another 10%; the other 80% get the winner."
                readOnly={locked}
                {...(errors.abSplitPercent !== undefined ? { error: errors.abSplitPercent } : {})}
              />
              <Select
                label="Pick the winner by"
                value={values.abMetric}
                onChange={(event) => set("abMetric", event.target.value as AbMetric)}
                options={METRIC_OPTIONS}
                helper="Turns on open or click tracking for this campaign, which picking a winner needs."
                disabled={locked}
                {...(errors.abMetric !== undefined ? { error: errors.abMetric } : {})}
              />
              <TextField
                label="Wait before picking (hours)"
                type="number"
                inputMode="decimal"
                min={0.25}
                max={168}
                step={0.25}
                value={values.abWaitHours}
                onChange={(event) => set("abWaitHours", event.target.value)}
                helper="Counted from when the campaign starts sending."
                readOnly={locked}
                {...(errors.abWaitHours !== undefined ? { error: errors.abWaitHours } : {})}
              />
            </>
          ) : null}
          </>
          ) : null}
          </FormSection>

          <div className="flex flex-wrap items-center gap-3">
            <Can permission="engage.campaign.campaign.write" scope="ORG">
              <Button type="submit" disabled={submitting || (campaign !== null && !dirty)}>
                {submitting
                  ? "Saving…"
                  : campaign === null
                    ? "Create draft"
                    : "Save draft"}
              </Button>
            </Can>
            {campaign !== null && dirty ? (
              <span className="text-sm text-muted">You have unsaved changes.</span>
            ) : null}
          </div>
        </form>
      </Card>

      <Card className="p-6 lg:col-span-2 lg:self-start">
        {values.channel === "email" ? (
          <CampaignPreview subject={values.subject} html={values.htmlBody} text={values.textBody} />
        ) : (
          <p className="whitespace-pre-wrap text-sm text-ink">
            {chosenTemplate?.preview_text ?? "Choose a template to see what recipients get."}
          </p>
        )}
      </Card>

      <SegmentFormDialog
        open={creatingSegment}
        lists={resolved.lists}
        onClose={() => setCreatingSegment(false)}
        onSaved={(segment) => {
          setSegments((current) => [...current, segment]);
          set("segmentId", segment.id);
          setCreatingSegment(false);
        }}
      />

      <TestSendDialog
        sender={testing}
        onClose={() => setTesting(null)}
        onSent={(sender, message) => {
          setTested((current) => ({ ...current, [sender.id]: sender }));
          setTesting(null);
          setTestNotice(message);
        }}
      />
    </div>
  );
}
