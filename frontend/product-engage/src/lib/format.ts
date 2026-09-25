import { formatInZone } from "@opsgrids/ui";

import type { CampaignStatus, ConnectionKind, DnsRecordKind } from "@/api/types";

/**
 * The few presentation decisions this product repeats, in one place.
 *
 * Every timestamp on the wire is an ISO-8601 UTC instant and is rendered in the browser's zone,
 * which for these screens is the right zone: "when did DNS last get checked" is a fact about the
 * person looking, not about a workspace calendar. Nothing here computes a date — the service is
 * the only thing that decides when anything happened.
 */

/** `2026-09-19T06:12:44Z` → `19 Sep 2026, 11:42`. */
export function formatInstant(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** 2000 → `2,000`. Tabular figures line these up in a column; the grouping makes them readable. */
export function formatNumber(n: number): string {
  return new Intl.NumberFormat(undefined).format(n);
}

/**
 * The provider names a customer recognises. `platform` is named although the wizard does not
 * offer it: it is a value the wire enum carries, and a label map missing a key renders
 * `undefined` at exactly the moment something unexpected arrives.
 */
const CONNECTION_KIND_LABELS: Record<ConnectionKind, string> = {
  google: "Google Workspace",
  microsoft: "Microsoft 365",
  smtp: "Your own SMTP server",
  platform: "OpsGrids-managed sending",
};

export function connectionKindLabel(kind: ConnectionKind): string {
  return CONNECTION_KIND_LABELS[kind];
}

/**
 * What each DNS record is for, in the customer's terms rather than the protocol's. They are
 * pasting these into a registrar. No `return_path` or `tracking` label exists any more: OpsGrids
 * publishes no mail infrastructure of its own, so neither record is ever generated.
 */
const DNS_RECORD_LABELS: Record<DnsRecordKind, string> = {
  spf: "SPF — who may send as this domain",
  dkim: "DKIM — the signature on every message",
  dmarc: "DMARC — what to do with mail that fails the two above",
};

export function dnsRecordLabel(kind: DnsRecordKind): string {
  return DNS_RECORD_LABELS[kind];
}

/**
 * A campaign's status as a word to say aloud, not the wire token: "sent", not `sent`; "paused",
 * not `paused` — and a status this app names in words in one place, so a live-region announcement
 * never reads out an enum.
 */
const CAMPAIGN_STATUS_WORDS: Record<CampaignStatus, string> = {
  draft: "a draft",
  scheduled: "scheduled",
  sending: "sending",
  paused: "paused",
  sent: "sent",
  cancelled: "cancelled",
};

export function campaignStatusWord(status: CampaignStatus): string {
  return CAMPAIGN_STATUS_WORDS[status];
}

export const IST_ZONE = "Asia/Kolkata";

/**
 * The IANA name a zone actually resolves to at runtime. "Asia/Kolkata" and its older alias
 * "Asia/Calcutta" name the same zone, but which spelling `Intl` hands back for "the browser's
 * own zone" depends on the ICU data it ships with — comparing the raw strings would then treat
 * an Indian browser's own zone as a *different* zone from IST and show the same instant twice.
 */
export function canonicalZone(zone: string): string {
  try {
    return new Intl.DateTimeFormat(undefined, { timeZone: zone }).resolvedOptions().timeZone;
  } catch {
    return zone;
  }
}

/**
 * A campaign's `scheduled_at`, read in the zone the browser reports plus, when that is not
 * India Standard Time, the IST reading beside it — the zone this platform's own support and
 * most of its customers keep. Never the zone the campaign was *scheduled in*: that choice ends
 * the moment the instant is fixed, and this is read by whoever is looking, wherever they are.
 */
export function formatScheduledFor(iso: string): string {
  const zone = Intl.DateTimeFormat().resolvedOptions().timeZone || IST_ZONE;
  const local = formatInZone(iso, zone);
  return canonicalZone(zone) === canonicalZone(IST_ZONE) ? local : `${local} (${formatInZone(iso, IST_ZONE)})`;
}
