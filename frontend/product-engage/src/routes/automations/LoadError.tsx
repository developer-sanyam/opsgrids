import { Card, ErrorState } from "@opsgrids/ui";

import { isNotActivated } from "@/lib/errors";
import { EngageNotActive } from "@/ui";

/**
 * A failed read, with a way to try again. Engage not being active is its own state and gets the
 * page that says where to start it; anything else (a timeout, a `500`, a dropped connection) is
 * an error the person can retry without reloading the page.
 */
export function LoadError({
  error,
  title,
  onRetry,
}: {
  error: Error;
  title: string;
  onRetry: () => void;
}) {
  if (isNotActivated(error)) return <EngageNotActive />;
  return (
    <Card className="p-6">
      <ErrorState title={title} message={error.message} onRetry={onRetry} />
    </Card>
  );
}
