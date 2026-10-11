/**
 * What a screen says when a reveal does not produce a value.
 *
 * The reveal endpoints answer a refusal with a problem document whose `detail` is the server's own
 * sentence ("Out of scope for this resource", "Missing permission: crm.lead.lead.read_sensitive").
 * Those are written for a log, not for a person in the middle of a call with a customer, so each
 * one that a person can meet is said again here, in a sentence that names what happened and what
 * to do next. The sentence the server writes for "nothing on file" and "no such record" already
 * reads that way, and is shown as it is.
 *
 * Nothing here decides anything: the server refused, this only words it.
 */

import { ApiError } from "../http";
import { problemMessage } from "../problem";

/** Whose details are being revealed, for the sentences that name them. */
export type RevealSubject = "lead" | "contact" | "employee";

export interface RevealNotice {
  /** `neutral`: the answer is "there is nothing to show". `error`: the reveal was refused or failed. */
  tone: "neutral" | "error";
  text: string;
}

const CANNOT_REACH = "Couldn't reveal this right now. Try again.";

const OUT_OF_SCOPE = /^out of scope/i;
const SECOND_FACTOR_REQUIRED = /^additional authentication required/i;
const NOTHING_ON_FILE = /\bon file\b/i;

function detailOf(error: ApiError): string {
  return error.problem?.detail ?? "";
}

/** A sentence from the server, finished the way ours are. */
function withPeriod(sentence: string): string {
  return /[.!?]$/.test(sentence) ? sentence : `${sentence}.`;
}

/**
 * The server wants a second factor that the token it was sent does not carry. Sending the same
 * token again would be refused the same way, so the caller drops it and asks for a new code.
 */
export function needsFreshCode(error: unknown): boolean {
  return error instanceof ApiError && error.status === 403 && SECOND_FACTOR_REQUIRED.test(detailOf(error));
}

/** The contact came back with no sealed number: not a failure, just nothing to show. */
export function nothingOnFile(noun: string): RevealNotice {
  return { tone: "neutral", text: `No ${noun} is on file.` };
}

export function revealFailure(error: unknown, subject: RevealSubject): RevealNotice {
  if (!(error instanceof ApiError)) {
    return { tone: "error", text: problemMessage(error, CANNOT_REACH) };
  }
  const detail = detailOf(error);
  switch (error.status) {
    case 401:
      // The step-up token lapsed between the code and the call.
      return { tone: "error", text: "That one-time code has expired. Select Reveal to enter a new one." };
    case 402:
      return { tone: "error", text: "Revealing details isn't part of your plan." };
    case 403:
      if (OUT_OF_SCOPE.test(detail)) {
        return {
          tone: "error",
          text: `This ${subject} is not assigned to you, so you cannot reveal its details.`,
        };
      }
      if (SECOND_FACTOR_REQUIRED.test(detail)) {
        return {
          tone: "error",
          text: "A one-time code is needed to reveal this. Select Reveal to enter one.",
        };
      }
      return {
        tone: "error",
        text: `You don't have permission to reveal this ${subject}'s details. Ask an administrator for access.`,
      };
    case 404:
      return {
        tone: NOTHING_ON_FILE.test(detail) ? "neutral" : "error",
        text: withPeriod(problemMessage(error)),
      };
    default:
      return { tone: "error", text: withPeriod(problemMessage(error)) };
  }
}
