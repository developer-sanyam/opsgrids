import type { DnsRecordOut } from "@/api/types";

/**
 * How far a domain is from sending, counted the way the service counts it.
 *
 * `domain.fold_domain_status` folds a domain to `verified` when every **required** record
 * resolves, and DMARC is deliberately not one of them. So this counts required records only: a
 * "4 of 5" that went green only once an optional DMARC record appeared would be telling the
 * customer to wait for something that was never holding them up.
 */
export function requiredProgress(records: DnsRecordOut[]): { found: number; total: number } {
  const required = records.filter((record) => record.required);
  return { found: required.filter((record) => record.verified).length, total: required.length };
}
