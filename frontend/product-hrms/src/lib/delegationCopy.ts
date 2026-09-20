/**
 * The approved copy for the delegations screen (§4.E), in one place so the route, the create form
 * and the revoke dialog never say the same fact two different ways. Nothing here is paraphrased —
 * every string is quoted from the spec, with the one adaptation the spec's own example forces: its
 * sentences were written against a specific name ("Rohan Mehta … him"), and a reusable copy
 * function cannot know a colleague's pronoun, so "them" stands in — the fact conveyed is identical.
 */

import type { TagTone } from "@opsgrids/ui";

import type { DelegationOut, DelegationStatus } from "@/api/types";

const NAME_UNAVAILABLE = "Employee record unavailable";

const STATUS_TAG: Record<DelegationStatus, { label: string; tone: TagTone }> = {
  active: { label: "Active", tone: "positive" },
  scheduled: { label: "Scheduled", tone: "info" },
  ended: { label: "Ended", tone: "neutral" },
  revoked: { label: "Revoked", tone: "neutral" },
};

/** The row's status `Tag` — trusts the server's `status` outright, never recomputed from the
 *  caller's clock (ADR-0006 §11: "computed server-side … never from the caller's browser clock"). */
export function delegationStatusTag(status: DelegationStatus): { label: string; tone: TagTone } {
  return STATUS_TAG[status];
}

/** §4.E — "Rohan Mehta decides for you" / "Rohan Mehta decides for Priya Nair" in ORG mode. */
export function delegationSentence(
  delegation: DelegationOut,
  myEmployeeId: string | null,
): string {
  const delegate = delegation.delegate_name ?? NAME_UNAVAILABLE;
  const isMine = myEmployeeId !== null && delegation.delegator_employee_id === myEmployeeId;
  return isMine
    ? `${delegate} decides for you`
    : `${delegate} decides for ${delegation.delegator_name ?? NAME_UNAVAILABLE}`;
}

/** §4.E — the revoke confirmation body, stated honestly either way: revocation never re-routes,
 *  and the delegator never lost eligibility. */
export function revokeConfirmationBody(
  delegation: DelegationOut,
  myEmployeeId: string | null,
): string {
  const delegate = delegation.delegate_name ?? NAME_UNAVAILABLE;
  const isMine = myEmployeeId !== null && delegation.delegator_employee_id === myEmployeeId;
  const forWhom = isMine ? "you" : (delegation.delegator_name ?? NAME_UNAVAILABLE);
  const whoseToDecide = isMine ? "yours" : `${delegation.delegator_name ?? NAME_UNAVAILABLE}'s`;
  return `${delegate} will stop being able to decide for ${forWhom} immediately. Anything already with them stays with them and is still ${whoseToDecide} to decide. This can't be undone.`;
}

/** §4.E — the pre-submit consequence note, shown above the form's actions before the caller
 *  commits: the re-pointing rule when the delegation starts immediately, or the honest absence of
 *  one when it starts later (ADR-0006 §5: "a future-dated delegation re-points nothing at
 *  creation"). */
export function delegationConsequenceNote(
  startsImmediately: boolean,
  startsAtDateText: string,
): string {
  return startsImmediately
    ? "Anything already waiting on you moves to them straight away. The due times don't change."
    : `This starts on ${startsAtDateText}. Anything waiting on you now stays with you until then.`;
}

/** §4.E — the "N requests moved" line, rendered **only** when `repointed_count > 0` (C4: "0 is a
 *  normal, common value … the client omits the line entirely rather than rendering '0 requests
 *  moved'"). Named by the delegate, matching the spec's own copy exactly. */
export function repointedLine(delegateName: string | null, repointedCount: number): string | null {
  if (repointedCount <= 0) return null;
  const delegate = delegateName ?? NAME_UNAVAILABLE;
  return repointedCount === 1
    ? `1 request waiting on you moved to ${delegate}.`
    : `${repointedCount} requests waiting on you moved to ${delegate}.`;
}

/** §4.E's create-success announcement: "Delegation set up. …" for an immediate start, "Delegation
 *  scheduled. …" for a future one — always naming who it runs for, "you" or the named delegator. */
export function delegationCreatedAnnouncement(
  delegateName: string | null,
  forSelf: boolean,
  delegatorName: string | null,
  startsImmediately: boolean,
  startsAtText: string,
): string {
  const delegate = delegateName ?? NAME_UNAVAILABLE;
  const forWhom = forSelf ? "you" : (delegatorName ?? NAME_UNAVAILABLE);
  const verb = startsImmediately ? "Delegation set up." : "Delegation scheduled.";
  return `${verb} ${delegate} decides for ${forWhom} from ${startsAtText}.`;
}

/** §4.E's revoke-success announcement — "for you" in the ordinary, personal case; named in the
 *  ORG "for another employee" case, on the same reasoning as `delegationSentence` above. */
export function delegationRevokedAnnouncement(
  delegateName: string | null,
  forSelf: boolean,
  delegatorName: string | null,
): string {
  const forWhom = forSelf ? "you" : (delegatorName ?? NAME_UNAVAILABLE);
  return `Delegation revoked. ${delegateName ?? NAME_UNAVAILABLE} can no longer decide for ${forWhom}.`;
}
