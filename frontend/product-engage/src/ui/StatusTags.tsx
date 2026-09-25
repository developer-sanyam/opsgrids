import { StatusBadge } from "@opsgrids/ui";
import type { StatusTone } from "@opsgrids/ui";

import type {
  CampaignStatus,
  ConnectionStatus,
  ConversationStatus,
  DomainStatus,
  RecipientStatus,
  SuppressionReason,
  TemplateStatus,
} from "@/api/types";

/**
 * The three status vocabularies this product renders, each translated in exactly one place, now
 * on `StatusBadge` and the shared tone registry (`opsgrids-ui/src/lib/status.ts`) rather than this
 * app's own colour choices.
 *
 * **The words carry the meaning and the tone only agrees with them**, because a customer who
 * cannot separate the green pill from the amber one still has to be able to tell a domain that
 * sends from one that does not. Each label is the service's own state in plain English rather
 * than its wire token: `pending` means the DNS is not published yet, and saying so is the whole
 * job of the pill.
 *
 * Where a word the registry knows means something different here than it does elsewhere in the
 * platform, this file keeps its own tone rather than the registry's — noted at each one.
 */

const DOMAIN_LABEL: Record<DomainStatus, string> = {
  // `verified` is folded from the *required* records alone, so this says "can send" rather than
  // "everything is published" — a domain with no DMARC record is verified and sends.
  verified: "Verified",
  pending: "Awaiting DNS",
  failed: "Check failed",
};

/** `verified`, `pending` and `failed` are registered words the registry already tones this way. */
export function DomainStatusTag({ status }: { status: DomainStatus }) {
  return <StatusBadge status={status} label={DOMAIN_LABEL[status]} />;
}

const CONNECTION_LABEL: Record<ConnectionStatus, string> = {
  connected: "Connected",
  disconnected: "Disconnected",
};

/** `connected` and `disconnected` are registered words the registry already tones this way. */
export function ConnectionStatusTag({ status }: { status: ConnectionStatus }) {
  return <StatusBadge status={status} label={CONNECTION_LABEL[status]} />;
}

/**
 * A sender's gate. `usable` is the service's derivation of `verified_at` — a test message actually
 * reached the provider — so the label names that event rather than an abstract state. Not a
 * registered word, so it keeps its own tone.
 */
export function SenderStatusTag({ usable }: { usable: boolean }) {
  return (
    <StatusBadge
      tone={usable ? "success" : "warning"}
      label={usable ? "Test message delivered" : "Not tested yet"}
    />
  );
}

/**
 * A WhatsApp template's approval state, exactly as `provider_templates.status` arrives. Every
 * value the wire carries has a case here — an unmapped status would fall through to `undefined`
 * at the one moment a customer most needs to know why a template cannot send. Never set by this
 * app: the label is a read of the service's own answer, not a claim this screen makes. Every one
 * of these is a registered word the registry already tones this way.
 */
const TEMPLATE_LABEL: Record<TemplateStatus, string> = {
  draft: "Draft — not submitted",
  pending: "Pending Meta review",
  approved: "Approved",
  rejected: "Rejected",
  paused: "Paused",
  disabled: "Disabled",
};

export function TemplateStatusTag({ status }: { status: TemplateStatus }) {
  return <StatusBadge status={status} label={TEMPLATE_LABEL[status]} />;
}

/**
 * Why an address may not be mailed. `manual` is the one a person wrote through this screen; the
 * other three are reports from the recipient or their mailbox provider, named as facts rather
 * than as a status this app decided. `unsubscribed` and `bounced` are registered words the
 * registry already tones this way; `complained` and `manual` are not registered, so they keep
 * their own tone.
 */
const SUPPRESSION_LABEL: Record<SuppressionReason, string> = {
  unsubscribed: "Unsubscribed",
  bounced: "Bounced",
  complained: "Complained",
  manual: "Added by hand",
};

/** `unsubscribed` and `bounced` are registered words, toned exactly as here; `complained` and
 *  `manual` aren't registered, so they take their own tone. One table either way, since `manual`
 *  isn't a `StatusWord` at all and `StatusBadge` can't take it as `status`. */
const SUPPRESSION_TONE: Record<SuppressionReason, StatusTone> = {
  unsubscribed: "neutral",
  bounced: "danger",
  complained: "danger",
  manual: "warning",
};

export function SuppressionReasonTag({ reason }: { reason: SuppressionReason }) {
  return <StatusBadge tone={SUPPRESSION_TONE[reason]} label={SUPPRESSION_LABEL[reason]} />;
}

/**
 * A conversation's triage state. Independent of the 24-hour service window — a closed
 * conversation can still be inside its window, and an open one can be well outside it — so this
 * is never a proxy for whether a reply is possible; that answer is `WindowStatusTag`'s.
 *
 * `open` means something different here than the registry's general sense of the word (an open
 * ticket, mid-flight) — an open conversation is the good state, not one waiting on anyone — so
 * both keep their own tone rather than the registry's.
 */
const CONVERSATION_TONE: Record<ConversationStatus, StatusTone> = {
  open: "success",
  closed: "neutral",
};

const CONVERSATION_LABEL: Record<ConversationStatus, string> = {
  open: "Open",
  closed: "Closed",
};

export function ConversationStatusTag({ status }: { status: ConversationStatus }) {
  return <StatusBadge tone={CONVERSATION_TONE[status]} label={CONVERSATION_LABEL[status]} />;
}

/**
 * A campaign's own lifecycle, exactly as `campaigns.status` arrives — draft through sent, and
 * cancelled from any state before that. `paused` is named as the send driver's own state rather
 * than folded into any other tone, because it is the one status that means "action wanted": the
 * campaign is not finished, not cancelled, and not making progress on its own.
 *
 * `draft` and `scheduled` are registered words the registry already tones this way. `sending`,
 * `paused`, `sent` and `cancelled` mean something more specific here than the registry's general
 * sense of those words, so they keep their own tone.
 */
const CAMPAIGN_LABEL: Record<CampaignStatus, string> = {
  draft: "Draft",
  scheduled: "Scheduled",
  sending: "Sending",
  paused: "Paused — needs attention",
  sent: "Sent",
  cancelled: "Cancelled",
};

const CAMPAIGN_TONE: Record<CampaignStatus, StatusTone> = {
  draft: "neutral",
  scheduled: "info",
  sending: "warning",
  paused: "danger",
  sent: "success",
  cancelled: "neutral",
};

export function CampaignStatusTag({ status }: { status: CampaignStatus }) {
  return <StatusBadge tone={CAMPAIGN_TONE[status]} label={CAMPAIGN_LABEL[status]} />;
}

/**
 * One recipient's outcome. `queued` already carries the driver's in-flight claim lease — see
 * `RecipientStatus`'s own comment in `api/types.ts` — so there is nothing this app renders that
 * the wire vocabulary does not also carry.
 *
 * `failed` is a registered word the registry already tones this way. `queued` and `sent` mean
 * something more specific here than the registry's general sense of those words, and `suppressed`
 * isn't registered at all, so all three keep their own tone.
 */
const RECIPIENT_LABEL: Record<RecipientStatus, string> = {
  queued: "Queued",
  sent: "Sent",
  failed: "Failed",
  suppressed: "Suppressed",
};

/** `failed` is a registered word, toned exactly as here; `queued` and `sent` mean something more
 *  specific here than the registry's general sense of those words, and `suppressed` isn't
 *  registered at all — so all four take their own tone in one table, since `suppressed` isn't a
 *  `StatusWord` at all and `StatusBadge` can't take it as `status`. */
const RECIPIENT_TONE: Record<RecipientStatus, StatusTone> = {
  queued: "neutral",
  sent: "success",
  failed: "danger",
  suppressed: "warning",
};

export function RecipientStatusTag({ status }: { status: RecipientStatus }) {
  return <StatusBadge tone={RECIPIENT_TONE[status]} label={RECIPIENT_LABEL[status]} />;
}

/** Whether a free-text reply is possible right now, exactly as the service computed it
 *  (`within_service_window`) — never re-derived from `last_customer_message_at` here, so this
 *  screen can never disagree with the reply endpoint about the one fact that decides it. Not a
 *  registered word, so it keeps its own tone. */
export function WindowStatusTag({ withinWindow }: { withinWindow: boolean }) {
  return (
    <StatusBadge tone={withinWindow ? "success" : "warning"} label={withinWindow ? "Can reply" : "Template only"} />
  );
}
