import type { ConnectionKind, DnsRecordKind } from "@/api/types";

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
 * pasting these into a registrar, and `return_path` is not a sentence anybody can act on.
 */
const DNS_RECORD_LABELS: Record<DnsRecordKind, string> = {
  spf: "SPF — who may send as this domain",
  dkim: "DKIM — the signature on every message",
  dmarc: "DMARC — what to do with mail that fails the two above",
  return_path: "Return path — where bounces go",
  tracking: "Tracking — opens and clicks on your own domain",
};

export function dnsRecordLabel(kind: DnsRecordKind): string {
  return DNS_RECORD_LABELS[kind];
}
