import { problemMessage } from "@opsgrids/client";

import type { OverrideResult } from "@/api/types";

/**
 * What a failed override says in the dialog's own alert — the failure that belongs to no field.
 *
 * The server's sentence, verbatim: a 4xx names what to change, and a 5xx says the owning service
 * could not be reached. Field-level detail goes on the field it names and is not repeated here —
 * **unless** it names a field this dialog has no control for, in which case it comes up here too,
 * because a validation failure that lands nowhere is the one that leaves an operator staring at a
 * form that will not submit and says nothing about why. `shown` lists the fields the dialog renders.
 */
export function failureText(
  error: Error | null,
  fieldErrors: Record<string, string>,
  shown: readonly string[],
): string | undefined {
  if (error === null) return undefined;
  const homeless = Object.entries(fieldErrors)
    .filter(([field]) => !shown.includes(field))
    .map(([field, message]) => `${field}: ${message}`);
  if (Object.keys(fieldErrors).length > 0 && homeless.length === 0) return undefined;
  const sentence = problemMessage(error, "The override was rejected.");
  return homeless.length > 0 ? `${sentence} ${homeless.join("; ")}` : sentence;
}

/** The toast a landed override raises: what happened, and the audit row that records it, if named. */
export function acceptedDescription(result: OverrideResult): string {
  return result.audit_id === null ? result.summary : `${result.summary} · audit ${result.audit_id}`;
}
