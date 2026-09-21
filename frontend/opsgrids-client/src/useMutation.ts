/**
 * The one way a screen performs a write.
 *
 * Everything a create / edit / delete flow needs to get right is here once, so that ten screens
 * do not each get a different two of them right:
 *
 * - **A second call while one is in flight is ignored.** The guard is a ref, not `pending`:
 *   state lands a render later, and two clicks inside one frame both read the stale `false`.
 *   A double-submitted POST is a duplicate row, and no amount of button-disabling fixes it
 *   from the other side of a re-render.
 * - **Field-level detail lands on the fields.** An `ApiError` whose problem document carries
 *   `errors[]` becomes `fieldErrors` keyed by field name; a form that says "something went
 *   wrong" at the top when it knows which field is wrong is failing the user on purpose.
 * - **Nothing is ever retried.** Not a 4xx — an authorization failure does not fix itself and
 *   retrying only makes the user wait to be told no — and not a 5xx either: a write is not
 *   idempotent unless the endpoint says so, and this hook cannot know. Retrying is the user's
 *   decision, made with a button.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "./http";
import { fieldErrorMap } from "./problem";

export interface MutationOptions<Input, Result> {
  /** Ran after the write lands. Close the dialog, toast, reload the list — in that order. */
  onSuccess?: ((result: Result, input: Input) => void) | undefined;
  /**
   * Ran after it fails, *in addition to* `error` being set. For the failures that are not a
   * message under a control: a 409 that should reload the row, a 403 that should re-route.
   */
  onError?: ((error: Error, input: Input) => void) | undefined;
}

export interface Mutation<Input, Result> {
  /**
   * Run the write. Resolves to the result, or to `undefined` when the call failed or was
   * skipped because one was already in flight — so `await mutate(x)` is never a success signal.
   * Branch in `onSuccess`.
   */
  mutate: (input: Input) => Promise<Result | undefined>;
  pending: boolean;
  /** The last failure, or `null`. Use `problemMessage(error)` for the sentence. */
  error: Error | null;
  /** `{field: message}` from the problem document's `errors[]`. Empty unless the server sent it. */
  fieldErrors: Record<string, string>;
  /** Clear `error` and `fieldErrors` — on dialog open, or when the user edits a failed field. */
  reset: () => void;
}

interface MutationInternalState {
  pending: boolean;
  error: Error | null;
  fieldErrors: Record<string, string>;
}

const IDLE: MutationInternalState = { pending: false, error: null, fieldErrors: {} };

/**
 * `fn` is the API call — `(input) => createWidget(input)`. It is read from a ref, so an inline
 * arrow at the call site does not change `mutate`'s identity and `mutate` is safe in a
 * dependency list.
 */
export function useMutation<Input, Result>(
  fn: (input: Input) => Promise<Result>,
  options: MutationOptions<Input, Result> = {},
): Mutation<Input, Result> {
  const [state, setState] = useState<MutationInternalState>(IDLE);

  const fnRef = useRef(fn);
  const optionsRef = useRef(options);
  useEffect(() => {
    fnRef.current = fn;
    optionsRef.current = options;
  });

  // The synchronous duplicate-submission guard. `state.pending` is the same fact one render
  // later, which is one render too late for a double click.
  const inFlight = useRef(false);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const reset = useCallback(() => {
    setState((prev) => (prev.pending ? { ...prev, error: null, fieldErrors: {} } : IDLE));
  }, []);

  const mutate = useCallback(async (input: Input): Promise<Result | undefined> => {
    if (inFlight.current) return undefined;
    inFlight.current = true;
    setState({ pending: true, error: null, fieldErrors: {} });

    let result: Result;
    try {
      result = await fnRef.current(input);
    } catch (thrown: unknown) {
      const error = thrown instanceof Error ? thrown : new Error(String(thrown));
      inFlight.current = false;
      if (mounted.current) {
        setState({
          pending: false,
          error,
          fieldErrors: error instanceof ApiError ? fieldErrorMap(error.problem) : {},
        });
      }
      // Fires even after unmount: a 409 handler that re-routes must still run when the dialog
      // that started the write has already gone.
      optionsRef.current.onError?.(error, input);
      return undefined;
    }

    inFlight.current = false;
    if (mounted.current) setState(IDLE);
    // Outside the try: a throw from the caller's own success handling is a bug in that handler,
    // not a failed write, and must not be reported as one.
    optionsRef.current.onSuccess?.(result, input);
    return result;
  }, []);

  return { mutate, pending: state.pending, error: state.error, fieldErrors: state.fieldErrors, reset };
}
