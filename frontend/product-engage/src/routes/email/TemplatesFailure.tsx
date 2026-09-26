import { Card, EmptyState, ErrorState, MailIcon } from "@opsgrids/ui";

import { isNotActivated } from "@/lib/errors";

/**
 * What a template read shows when it fails.
 *
 * Templates are served by the `notification` service, behind its own product entitlement rather
 * than `engage`'s, so a `402` here is a different fact from the one `LoadFailure` reports. It is
 * the plan-gated state: nothing is broken and nobody did anything wrong, and this says which part
 * of the plan is missing. Everything else is a real failure with a retry.
 */
export function TemplatesFailure({ error, onRetry }: { error: Error; onRetry: () => void }) {
  if (isNotActivated(error)) {
    return (
      <Card className="p-6">
        <EmptyState
          icon={<MailIcon className="size-6" />}
          title="Email templates aren't part of this organization's plan yet"
          description="Message templates are served by the notification service, which isn't active here. Someone who manages your organization's products, usually an owner or admin, can turn it on from Products in the OpsGrids dashboard."
        />
      </Card>
    );
  }
  return (
    <Card className="p-6">
      <ErrorState message={error.message} title="Couldn't load your email templates" onRetry={onRetry} />
    </Card>
  );
}
