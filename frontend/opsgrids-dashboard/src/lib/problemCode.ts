import { ApiError } from "@opsgrids/client";

/**
 * `errors[0].code` — a service's stable refusal code (`state_changed`, `not_offerable`, …), when the
 * problem document carries one. Subscription's coded refusals name no field, so `fieldErrors`
 * drops them; a screen that branches on the code reads it here.
 */
export function problemCode(error: unknown): string | null {
  if (!(error instanceof ApiError)) return null;
  return error.problem?.errors?.[0]?.code ?? null;
}
