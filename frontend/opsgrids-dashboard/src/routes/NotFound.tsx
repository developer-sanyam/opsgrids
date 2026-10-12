import { Link } from "react-router-dom";

import { Card, PageHeader } from "@/ui";

/** The catch-all. A 404 is a fact, not a failure — it says so and points somewhere useful. */
export function NotFoundPage() {
  return (
    <div className="flex flex-col gap-6">
      <PageHeader eyebrow="OpsGrids" title="That page doesn't exist" />
      <Card className="flex flex-col items-start gap-3 p-8">
        <p className="max-w-prose text-sm text-muted">Nothing lives at this address. The products page is the start of everything here.</p>
        <Link
          to="/"
          className="inline-flex h-11 items-center rounded-full bg-accent px-5 font-semibold text-on-accent transition-colors hover:bg-accent-strong"
        >
          Back to products
        </Link>
      </Card>
    </div>
  );
}
