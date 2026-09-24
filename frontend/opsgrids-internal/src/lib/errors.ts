import { ApiError } from "@opsgrids/client";

/**
 * The problem document's `request_id`, or nothing. Support asks for it and a person should never
 * have to find it, so a failure that has one carries it onto the screen as `Reference: …`.
 */
export function referenceOf(error: unknown): string | undefined {
  return error instanceof ApiError ? error.problem?.request_id : undefined;
}
