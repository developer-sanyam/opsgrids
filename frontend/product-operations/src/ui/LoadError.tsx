import { ApiError, problemMessage } from "@opsgrids/client";

import { ErrorState } from "@opsgrids/ui";

interface LoadErrorProps {
  error: Error;
  /** What failed to load, as a plural noun: "requisitions", "goods receipts". */
  noun: string;
  onRetry?: (() => void) | undefined;
}

/**
 * The error half of an async surface, told apart by what actually happened: a 403 is not a
 * failure and is not fixed by retrying (so it offers no retry), a 404 is a different fact, and a
 * dropped connection says so instead of a server sentence that does not exist.
 */
export function LoadError({ error, noun, onRetry }: LoadErrorProps) {
  if (error instanceof ApiError && error.status === 403) {
    return (
      <ErrorState
        tone="neutral"
        title={`You don't have access to ${noun}`}
        message={problemMessage(error, "Ask an administrator for access if you need it.")}
      />
    );
  }
  if (error instanceof ApiError && error.status === 404) {
    return <ErrorState tone="neutral" title="Not found" message={problemMessage(error)} />;
  }
  if (!(error instanceof ApiError)) {
    return (
      <ErrorState
        title="Can't reach the server"
        message="Check your connection, then try again."
        {...(onRetry ? { onRetry } : {})}
      />
    );
  }
  const reference = error.problem?.request_id;
  return (
    <ErrorState
      title={`Couldn't load ${noun}`}
      message={problemMessage(error)}
      {...(reference !== undefined ? { reference } : {})}
      {...(onRetry ? { onRetry } : {})}
    />
  );
}
