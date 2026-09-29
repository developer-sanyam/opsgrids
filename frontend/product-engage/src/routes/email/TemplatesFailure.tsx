import { Card, ErrorState } from "@opsgrids/ui";

import { isNotActivated } from "@/lib/errors";
import { EngageNotActive } from "@/ui";

/**
 * What a template read shows when it fails.
 *
 * Templates are read and written through Engage's own route (`GET`/`PUT /engage/email/templates`,
 * gated on `engage.email.template.read`/`.write`) rather than `notification`'s permission of the
 * same shape, so a `402` here means the same thing every other Engage screen's `402` does: Engage
 * itself is not active, not that some other product is missing. `EngageNotActive` is the ordinary
 * not-active state every other Engage list shows for that; everything else is a real failure with
 * a retry.
 */
export function TemplatesFailure({ error, onRetry }: { error: Error; onRetry: () => void }) {
  if (isNotActivated(error)) return <EngageNotActive />;
  return (
    <Card className="p-6">
      <ErrorState message={error.message} title="Couldn't load your email templates" onRetry={onRetry} />
    </Card>
  );
}
