/**
 * `problem.errors[]` — the field-level detail a 422 carries (`platform_core.errors.ValidationProblem`).
 *
 * `@opsgrids/client`'s `ProblemJson` does not declare `errors` (RFC 7807 leaves extension members
 * to the API), so this is typed here rather than by widening the SDK's own type, which this app
 * does not own.
 */

import type { ProblemJson } from "@opsgrids/client";

export interface FieldError {
  field: string;
  code: string;
  message: string;
}

interface ProblemWithErrors extends ProblemJson {
  errors?: FieldError[];
}

/** Every field error on a 422, or an empty array when the problem carries none. */
export function fieldErrors(problem: ProblemJson | null): FieldError[] {
  return (problem as ProblemWithErrors | null)?.errors ?? [];
}

/** The message for one field, verbatim (§2.5: never a rewritten, friendlier version of it). */
export function fieldError(problem: ProblemJson | null, field: string): string | undefined {
  return fieldErrors(problem).find((e) => e.field === field)?.message;
}
