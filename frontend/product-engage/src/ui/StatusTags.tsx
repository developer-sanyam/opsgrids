import { Tag } from "@opsgrids/ui";
import type { TagTone } from "@opsgrids/ui";

import type { ConnectionStatus, DomainStatus, SuppressionReason, TemplateStatus } from "@/api/types";

/**
 * The three status vocabularies this product renders, each translated in exactly one place.
 *
 * **The words carry the meaning and the tone only agrees with them**, because a customer who
 * cannot separate the green pill from the amber one still has to be able to tell a domain that
 * sends from one that does not. Each label is the service's own state in plain English rather
 * than its wire token: `pending` means the DNS is not published yet, and saying so is the whole
 * job of the pill.
 */

const DOMAIN_LABELS: Record<DomainStatus, { label: string; tone: TagTone }> = {
  // `verified` is folded from the *required* records alone, so this says "can send" rather than
  // "everything is published" — a domain with no DMARC record is verified and sends.
  verified: { label: "Verified", tone: "positive" },
  pending: { label: "Awaiting DNS", tone: "warning" },
  failed: { label: "Check failed", tone: "negative" },
};

export function DomainStatusTag({ status }: { status: DomainStatus }) {
  const { label, tone } = DOMAIN_LABELS[status];
  return <Tag tone={tone}>{label}</Tag>;
}

const CONNECTION_LABELS: Record<ConnectionStatus, { label: string; tone: TagTone }> = {
  connected: { label: "Connected", tone: "positive" },
  disconnected: { label: "Disconnected", tone: "negative" },
};

export function ConnectionStatusTag({ status }: { status: ConnectionStatus }) {
  const { label, tone } = CONNECTION_LABELS[status];
  return <Tag tone={tone}>{label}</Tag>;
}

/**
 * A sender's gate. `usable` is the service's derivation of `verified_at` — a test message actually
 * reached the provider — so the label names that event rather than an abstract state.
 */
export function SenderStatusTag({ usable }: { usable: boolean }) {
  return usable ? (
    <Tag tone="positive">Test message delivered</Tag>
  ) : (
    <Tag tone="warning">Not tested yet</Tag>
  );
}

/**
 * A WhatsApp template's approval state, exactly as `provider_templates.status` arrives. Every
 * value the wire carries has a case here — an unmapped status would fall through to `undefined`
 * at the one moment a customer most needs to know why a template cannot send. Never set by this
 * app: the label is a read of the service's own answer, not a claim this screen makes.
 */
const TEMPLATE_LABELS: Record<TemplateStatus, { label: string; tone: TagTone }> = {
  draft: { label: "Draft — not submitted", tone: "neutral" },
  pending: { label: "Pending Meta review", tone: "warning" },
  approved: { label: "Approved", tone: "positive" },
  rejected: { label: "Rejected", tone: "negative" },
  paused: { label: "Paused", tone: "warning" },
  disabled: { label: "Disabled", tone: "negative" },
};

export function TemplateStatusTag({ status }: { status: TemplateStatus }) {
  const { label, tone } = TEMPLATE_LABELS[status];
  return <Tag tone={tone}>{label}</Tag>;
}

/**
 * Why an address may not be mailed. `manual` is the one a person wrote through this screen; the
 * other three are reports from the recipient or their mailbox provider, named as facts rather
 * than as a status this app decided.
 */
const SUPPRESSION_LABELS: Record<SuppressionReason, { label: string; tone: TagTone }> = {
  unsubscribed: { label: "Unsubscribed", tone: "neutral" },
  bounced: { label: "Bounced", tone: "negative" },
  complained: { label: "Complained", tone: "negative" },
  manual: { label: "Added by hand", tone: "warning" },
};

export function SuppressionReasonTag({ reason }: { reason: SuppressionReason }) {
  const { label, tone } = SUPPRESSION_LABELS[reason];
  return <Tag tone={tone}>{label}</Tag>;
}
