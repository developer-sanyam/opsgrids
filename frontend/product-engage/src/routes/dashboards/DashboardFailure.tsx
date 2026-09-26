import { ApiError } from "@opsgrids/client";
import { Card, EmptyState, ErrorState, ShieldIcon } from "@opsgrids/ui";

import { isNotActivated } from "@/lib/errors";
import { EngageNotActive } from "@/ui";

/**
 * What a dashboard shows when its read fails, told apart by what actually happened — because the
 * three outcomes call for three different next steps and one generic "something went wrong"
 * serves none of them:
 *
 * - `402`: Engage is not active for the organization. The plan-gated state, with the way out.
 * - `403`: the caller's role does not include this dashboard. A state, not a failure, and it says
 *   so in the role's terms. It is presentation only — the service refuses the read on its own.
 * - anything else, including a request that never reached the gateway: an error with a retry.
 *   Never retried automatically for a 4xx; the person decides.
 */
export function DashboardFailure({
  error,
  title,
  what,
  onRetry,
}: {
  error: Error;
  /** The heading for a real failure: "Couldn't load the WhatsApp overview". */
  title: string;
  /** What the person tried to open, lower case: "the WhatsApp overview". */
  what: string;
  onRetry: () => void;
}) {
  if (isNotActivated(error)) return <EngageNotActive />;

  if (error instanceof ApiError && error.status === 403) {
    return (
      <Card className="p-6">
        <EmptyState
          icon={<ShieldIcon className="size-6" />}
          title={`You can't open ${what}`}
          description="Your role doesn't include seeing these figures. Someone who manages roles in your organization can add the permission."
        />
      </Card>
    );
  }

  const message =
    error instanceof ApiError
      ? error.message
      : "Couldn't reach the server. Check your connection and try again.";
  return (
    <Card className="p-6">
      <ErrorState title={title} message={message} onRetry={onRetry} />
    </Card>
  );
}
