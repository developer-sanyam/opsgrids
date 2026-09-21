/**
 * `problem.errors[]` — the field-level detail the platform's problem documents carry.
 *
 * RFC 7807 leaves extension members to the API, and this platform defines exactly one:
 * `errors`, an array of `{field, code, message}`. It is produced in two places, both in
 * `libs/platform-core/src/platform_core/errors.py`:
 *
 * - The `RequestValidationError` handler (line 148) turns every pydantic error into one entry,
 *   with `field` built from the error's `loc` joined by `.` **minus** the leading `body` /
 *   `query` segment — so a client keys on `"name"`, or `"items.0.sku"` for a nested one, never
 *   on `"body.name"`.
 * - `ProblemError(detail, errors=[…])` (line 31), which every service uses to attach a field to
 *   a hand-raised 422 or 409 — `services/approval/app/services.py` and `services/storefront`
 *   both do, with the same three keys.
 *
 * There is no `invalid-params` member and no raw pydantic `loc` on the wire. Read `field`.
 *
 * Entries may carry extra keys (`account` adds `employee_id`); they are ignored here rather than
 * typed, because only the three below are guaranteed.
 */

import { ApiError } from "./http";
import type { ProblemJson } from "./http";

/** One field-level failure. `message` is the server's sentence and is shown verbatim. */
export interface FieldError {
  /** The dotted path to the control, already stripped of its `body` / `query` prefix. */
  field: string;
  /** A stable string a screen may branch on — `self_delegation`, `string_too_short`. */
  code: string;
  message: string;
}

function isFieldError(value: unknown): value is FieldError {
  if (typeof value !== "object" || value === null) return false;
  const entry = value as Record<string, unknown>;
  return (
    typeof entry.field === "string" &&
    typeof entry.code === "string" &&
    typeof entry.message === "string"
  );
}

/**
 * Every well-formed field error on a problem document, or an empty array when it carries none.
 *
 * Entries that are not `{field, code, message}` are dropped rather than trusted: the body comes
 * off the wire, and a half-shaped entry rendered under a control is worse than no message.
 */
export function fieldErrors(problem: ProblemJson | null | undefined): FieldError[] {
  const raw = problem?.errors;
  if (!Array.isArray(raw)) return [];
  return raw.filter(isFieldError);
}

/** The message for one field, verbatim. Never a rewritten, friendlier version of it. */
export function fieldError(
  problem: ProblemJson | null | undefined,
  field: string,
): string | undefined {
  return fieldErrors(problem).find((entry) => entry.field === field)?.message;
}

/**
 * The whole set as `{field: message}`, ready to spread across a form's controls.
 *
 * First entry wins when a field fails twice — the server orders them as it validated them, and
 * a control shows one message.
 */
export function fieldErrorMap(problem: ProblemJson | null | undefined): Record<string, string> {
  const map: Record<string, string> = {};
  for (const entry of fieldErrors(problem)) {
    if (!(entry.field in map)) map[entry.field] = entry.message;
  }
  return map;
}

/**
 * The sentence to put in front of the user for a failure that is not a field's fault.
 *
 * The gateway's `detail` is usually already right — "That employee is already linked to another
 * member of this organization" needs no help. A non-`ApiError` (a dropped connection, a parse
 * failure) has no problem document and gets the fallback, because its `message` is machinery.
 */
export function problemMessage(error: unknown, fallback = "Something went wrong. Try again."): string {
  if (error instanceof ApiError) {
    return error.problem?.detail ?? error.problem?.title ?? error.message;
  }
  return fallback;
}
