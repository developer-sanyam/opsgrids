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
  return {
    found: required.filter((record) => record.status === "verified").length,
    total: required.length,
  };
}

/**
 * The service writes menu paths as "Apps -> Google Workspace -> Gmail". On a phone the line breaks
 * between the hyphen and the bracket ("Apps - > Google Workspace"), which reads as a typo, so
 * the arrow is drawn as the single character it stands for.
 */
export function readableInstruction(instruction: string): string {
  return instruction.replaceAll(" -> ", " \u2192 ");
}
