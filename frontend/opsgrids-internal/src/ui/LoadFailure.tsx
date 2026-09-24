import { ApiError, problemMessage } from "@opsgrids/client";
import { ErrorState } from "@opsgrids/ui";

import { ContractMismatchError } from "@/api/shape";
import { referenceOf } from "@/lib/errors";

/**
 * What a screen shows when its read fails, told apart by what the server said.
 *
 * A 403 is not a fault and not retryable: the operator is signed in, and their role does not
 * include this read — the server's own sentence names the roles that do. A 404 on an operator
 * route is the same answer an unknown path gets, so it says "not found" and offers no retry
 * either. An answer in a shape the console does not read (`ContractMismatchError`) is neither the
 * operator's fault nor transient, so it is named as what it is and not retried. Everything else is
 * a real failure and keeps the "try again" that a transient one deserves. Never retry a 4xx: it
 * does not fix itself, and retrying only makes the operator wait to be told no again.
 *
 * `title` is the screen's own heading for a real failure ("Couldn't load accounts").
 */
export function LoadFailure({
  error,
  title,
  onRetry,
}: {
  error: Error;
  title: string;
  onRetry?: (() => void) | undefined;
}) {
  const reference = referenceOf(error);
  if (error instanceof ContractMismatchError) {
    // Not the operator's doing and not transient: the API answered, in a shape this console does
    // not read yet. Retrying asks the same question and gets the same answer.
    return (
      <ErrorState
        tone="warning"
        title="This screen and the operator API are out of step"
        message={error.message}
      />
    );
  }
  if (error instanceof ApiError && error.status === 403) {
    return (
      <ErrorState
        tone="warning"
        title="You can't view this"
        message={problemMessage(error)}
        reference={reference}
      />
    );
  }
  if (error instanceof ApiError && error.status === 404) {
    return (
      <ErrorState tone="neutral" title="Not found" message={problemMessage(error)} reference={reference} />
    );
  }
  return (
    <ErrorState title={title} message={problemMessage(error)} reference={reference} onRetry={onRetry} />
  );
}
