/**
 * A thrown value as one sentence for a row of the report. The server's own sentence wins: it knows
 * the rule that was broken. Machinery (a dropped connection, a parse failure) gets a generic
 * sentence, because `error.message` there is for developers.
 */

import { ApiError } from "../http";
import { fieldErrors, problemMessage } from "../problem";
import { RowProblem } from "./types";

export const NETWORK_MESSAGE = "The request could not be completed. Check your connection and try again.";
export const PREPARE_MESSAGE = "This row could not be prepared for import.";
/** The result of an update row that already matches its record: nothing was sent for it. */
export const UNCHANGED_MESSAGE = "Every cell already matches the record, so nothing was sent.";

/** A sentence reduced to what makes it the same sentence: no case, no edge or inner spacing, no closing full stop. */
function sameSentence(text: string): string {
  return text.trim().replace(/\s+/g, " ").replace(/\.$/, "").toLowerCase();
}

export function rowMessage(error: unknown): string {
  if (error instanceof RowProblem) return error.message;
  if (error instanceof ApiError) {
    const headline = problemMessage(error);
    // The server often says the same sentence in `detail` and again as the one field error
    // ("A supplier coded 'X' already exists (code: A supplier coded 'X' already exists)").
    const said = sameSentence(headline);
    const fields = fieldErrors(error.problem)
      .filter((entry) => sameSentence(entry.message) !== said)
      .map((entry) => `${entry.field}: ${entry.message}`);
    return fields.length > 0 ? `${headline} (${fields.join("; ")})` : headline;
  }
  return NETWORK_MESSAGE;
}

/** A row that cannot be prepared: its own sentence for a `RowProblem`, else a generic one. */
export function prepareMessage(error: unknown): string {
  return error instanceof RowProblem ? error.message : PREPARE_MESSAGE;
}

export function isAbortError(error: unknown): boolean {
  return typeof error === "object" && error !== null && "name" in error && error.name === "AbortError";
}
