import { ApiError } from "@/api/http";
import { staffRefusalGuidance } from "@/lib/staffSubscriptionCopy";

/** What a staff-action dialog reports when something failed: the server's sentence verbatim, the
 *  reference to quote, and the next step its code implies. */
export interface Failure {
  message: string;
  reference: string | undefined;
  guidance: string | null;
}

export function failureOf(error: unknown): { failure: Failure; status: number | null; code: string | null } {
  if (error instanceof ApiError) {
    return {
      failure: {
        message: error.message,
        reference: error.requestId ?? undefined,
        guidance: staffRefusalGuidance(error.code),
      },
      status: error.status,
      code: error.code,
    };
  }
  // No answer at all (offline, a reset connection): for a write, the outcome is not known —
  // `failureKind` reads that from the missing status.
  const message = error instanceof Error ? error.message : "The request did not complete.";
  return { failure: { message, reference: undefined, guidance: null }, status: null, code: null };
}
