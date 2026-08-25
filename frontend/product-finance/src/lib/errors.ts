/**
 * Turning a failed read into a sentence that says what to do next.
 *
 * The gateway answers with RFC 7807, and its `detail` is often exactly right — "That invoice is
 * not part of this organization" needs no help from us. Two statuses do need help, and both are
 * ordinary on the compliance screens rather than exceptional:
 *
 * **403** is the designed outcome of a narrower grant. Reading the receivable book and reading
 * what was reported to the tax authority are separate permissions on purpose, so a finance user
 * who can see every invoice may legitimately not see a filing history. "Forbidden" leaves them
 * guessing; naming the permission tells them what to ask for.
 *
 * **404** on a collection means the service is not serving that surface — a deployment that
 * predates it, most often. That is a very different problem from an empty queue, and a screen
 * that blurs the two sends somebody looking for missing documents that were never there.
 */

import { ApiError } from "@atmora/client";

/** The permission each compliance surface needs, for the sentence a 403 gets. */
const PERMISSION: Record<string, string> = {
  "the filing history": "finance.compliance.einvoice.read",
  "the reconciliation queue": "finance.compliance.ims.read",
  "this supplier document": "finance.compliance.ims.read",
};

/**
 * `subject` names what could not be read, and is used mid-sentence — "the reconciliation queue",
 * "the filing history".
 */
export function readErrorMessage(error: Error, subject: string): string {
  if (!(error instanceof ApiError)) return error.message;

  if (error.status === 403) {
    const permission = PERMISSION[subject];
    return permission === undefined
      ? `You do not have permission to read ${subject}.`
      : `You do not have permission to read ${subject}. It is granted separately from the rest of the books — an administrator can add ${permission}.`;
  }
  if (error.status === 404) {
    return `This finance service does not serve ${subject}. That is a deployment gap rather than an empty queue — nothing here is missing data.`;
  }

  const detail = error.problem?.detail;
  return detail !== undefined && detail !== "Not Found"
    ? detail
    : `Could not read ${subject} (HTTP ${error.status}).`;
}
