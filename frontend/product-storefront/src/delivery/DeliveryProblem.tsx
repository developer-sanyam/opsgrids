import { ApiError } from "@opsgrids/client";

import { ErrorState } from "@opsgrids/ui";

/**
 * A failed load, said as the thing that actually happened.
 *
 * "Something went wrong" is right for a 5xx and wrong for everything else. A 403 is a permission
 * fact and names the permission to ask for; a 402 is a plan fact and is not the caller's mistake;
 * an offline browser is neither. Retrying a 4xx is never offered — an authorization failure does
 * not fix itself, and a retry button only makes the user wait to be told no again.
 *
 * **None of this is enforcement.** The server decided; this renders its decision.
 */
export function DeliveryProblem({
  error,
  onRetry,
  subject,
}: {
  error: Error;
  onRetry: () => void;
  /** What failed to load, for the 5xx wording: "The zone list", "The demand report". */
  subject: string;
}) {
  if (error instanceof ApiError) {
    const reference = error.problem?.request_id;

    if (error.status === 403) {
      return (
        <ErrorState
          tone="neutral"
          title="You can't see delivery zones"
          message="This needs storefront.delivery.zone.read at organization scope. An owner or a storefront admin can grant it."
          {...(reference !== undefined ? { reference } : {})}
        />
      );
    }
    if (error.status === 402) {
      return (
        <ErrorState
          tone="neutral"
          title="Storefront isn't on this organization's plan"
          message="Delivery zones come with the Storefront product. An owner can add it from billing, and this screen works the moment they do."
          {...(reference !== undefined ? { reference } : {})}
        />
      );
    }
    if (error.status === 404) {
      return (
        <ErrorState
          tone="neutral"
          title="There is no storefront here yet"
          message="Delivery zones belong to a store. Adopt a theme first and this screen has something to configure."
          {...(reference !== undefined ? { reference } : {})}
        />
      );
    }
    if (error.status === 429) {
      return (
        <ErrorState
          tone="warning"
          title="Too many requests"
          message="Wait a few seconds and try again."
          onRetry={onRetry}
          {...(reference !== undefined ? { reference } : {})}
        />
      );
    }
    return (
      <ErrorState
        title={`${subject} didn't load`}
        message={`${error.message} Nothing was changed.`}
        onRetry={onRetry}
        {...(reference !== undefined ? { reference } : {})}
      />
    );
  }

  return (
    <ErrorState
      tone="warning"
      title="You're offline, or the server is unreachable"
      message={`${subject} couldn't be fetched. Nothing was changed — try again once you're back on.`}
      onRetry={onRetry}
    />
  );
}
