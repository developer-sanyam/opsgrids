import { useMutation } from "@opsgrids/client";
import { useIdempotencyKey } from "@/lib/idempotency";
import { formDialogError } from "@/lib/formError";
import type { FormDialogErrorProps } from "@/lib/formError";
import { operationFieldErrors, stockShortfall } from "@/lib/stockErrors";
import type { StockShortfall } from "@/lib/stockErrors";

export interface StockOperation<Input> {
  /** Send the request. Ignored while one is in flight; a repeat of the same body replays. */
  submit: (input: Input) => void;
  pending: boolean;
  /** `{field: sentence}` from the server's `errors[]`, in the words the dialog shows. */
  fieldErrors: Record<string, string>;
  /** The 409 `insufficient-stock` detail, with the reservations in the way, or `null`. */
  shortfall: StockShortfall | null;
  /** The raw failure, for a dialog that words one status (a 403) itself. */
  error: Error | null;
  /** Spread onto `FormDialog`: the failure that belongs to no control the dialog shows. */
  dialogError: FormDialogErrorProps;
}

/**
 * The write half every stock dialog shares: one `Idempotency-Key` per distinct body, the
 * double-submit guard from `useMutation`, and the refusal split into the three places it belongs
 * — a field, the shortfall panel, or the dialog's own error line. Entered data is never touched
 * here, so a failure leaves the form exactly as the person left it.
 *
 * `fields` names the controls the dialog is showing right now. A server refusal about a field with
 * no control here (say `lot_id` on an item the dialog treats as untracked) would otherwise be
 * swallowed — the dialog would hide its general error because "a field error exists" and no field
 * would show it — so any such refusal turns the general error line on instead.
 */
export function useStockOperation<Input, Result>(
  call: (input: Input, idempotencyKey: string) => Promise<Result>,
  onSaved: (result: Result) => void,
  fields: readonly string[],
): StockOperation<Input> {
  const keyFor = useIdempotencyKey();
  const mutation = useMutation((input: Input) => call(input, keyFor(input)), {
    onSuccess: (result) => onSaved(result),
  });
  const fieldErrors = operationFieldErrors(mutation.error);
  const hasUnattached = Object.keys(fieldErrors).some(
    (field) => !fields.includes(field.replace(/^lines\.\d+\./, "")),
  );
  return {
    submit: (input) => void mutation.mutate(input),
    pending: mutation.pending,
    fieldErrors,
    shortfall: stockShortfall(mutation.error),
    error: mutation.error,
    dialogError: formDialogError(mutation.error, hasUnattached ? {} : fieldErrors),
  };
}
