import { ApiError, problemMessage } from "@opsgrids/client";

import { ErrorState } from "@opsgrids/ui";

import { GoBackButton } from "./GoBackButton";

interface LoadErrorProps {
  error: Error;
  /** What failed to load, as a plural noun: "requisitions", "goods receipts". */
  noun: string;
  onRetry?: (() => void) | undefined;
}

/**
 * What to tell a person when the server failed and said nothing useful. The transport's own
 * sentence ("Request failed with 500") is for a log, not for someone standing in a warehouse.
 */
function serverMessage(error: ApiError, noun: string): string {
  const said = error.problem?.detail ?? error.problem?.title;
  if (said !== undefined) return said;
  if (error.status >= 500) {
    return `The server had a problem loading ${noun}. Nothing you entered is lost. Try again in a moment.`;
  }
  return problemMessage(error);
}

/**
 * The error half of an async surface, told apart by what actually happened: a 403 is not a
 * failure and is not fixed by retrying (so it offers a way back, not a retry), a 404 is a
 * different fact, and a dropped connection says so instead of a server sentence that does not
 * exist.
 */
export function LoadError({ error, noun, onRetry }: LoadErrorProps) {
  if (error instanceof ApiError && error.status === 403) {
    return (
      <ErrorState
        tone="neutral"
        title={`You don't have access to ${noun}`}
        message={problemMessage(error, "Ask an administrator for access if you need it.")}
        action={<GoBackButton />}
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
      message={serverMessage(error, noun)}
      {...(reference !== undefined ? { reference } : {})}
      {...(onRetry ? { onRetry } : {})}
    />
  );
}
