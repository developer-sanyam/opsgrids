import { ApiError, problemMessage } from "@opsgrids/client";

/** The `FormDialog`/`ConfirmDialog` `error`/`reference` pair, spread onto the dialog. */
export interface FormDialogErrorProps {
  error?: string;
  reference?: string;
}

/**
 * The failure that belongs to no single field — a 409, a 403, a 500 — as `FormDialog`'s own
 * `error`/`reference` props. Empty once any field-level errors have landed on their own controls,
 * so the same failure is never said twice.
 */
export function formDialogError(
  error: Error | null,
  fieldErrors: Record<string, string>,
): FormDialogErrorProps {
  if (!error || Object.keys(fieldErrors).length > 0) return {};
  const requestId = error instanceof ApiError ? error.problem?.request_id : undefined;
  return {
    error: problemMessage(error),
    ...(requestId !== undefined ? { reference: requestId } : {}),
  };
}
