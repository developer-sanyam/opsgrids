/**
 * What `run` rejects with when the person closes the code dialog instead of answering it, or the
 * screen that asked goes away while it is open.
 *
 * Not a failure: nobody did anything wrong and there is nothing to report. It is a rejection
 * rather than a resolved `undefined` so the reveal that never happened cannot be mistaken for a
 * reveal that returned nothing — a caller that ignores it and reads the result gets an exception,
 * not a blank field. Catch it with `isStepUpCancelled` and do nothing.
 */
export class StepUpCancelledError extends Error {
  constructor() {
    super("The step-up prompt was closed without a code.");
    this.name = "StepUpCancelledError";
  }
}

export function isStepUpCancelled(error: unknown): error is StepUpCancelledError {
  return error instanceof StepUpCancelledError;
}
