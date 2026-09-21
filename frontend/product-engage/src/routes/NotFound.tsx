import { Link } from "react-router-dom";

import { Card, EmptyState, PageHeader } from "@/ui";

/** An address this app has no screen for. It says so and offers the one way back that works. */
export function NotFoundPage() {
  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader eyebrow="Engage" title="Page not found" />
      <Card className="p-6">
        <EmptyState
          title="There's nothing at that address"
          description="The link may be from a later phase of Engage, or simply mistyped."
          action={
            <Link
              to="/"
              className="inline-flex h-9 items-center rounded-full bg-ink px-4 text-sm font-semibold text-white transition-colors hover:bg-ink-soft focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink"
            >
              Back to the overview
            </Link>
          }
        />
      </Card>
    </div>
  );
}
