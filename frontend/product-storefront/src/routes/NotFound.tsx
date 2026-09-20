import { Link } from "react-router-dom";

import { Card, PageHeader } from "@/ui";

/** The catch-all. A 404 is a fact, not a failure — it says so and points somewhere useful. */
export function NotFoundPage() {
  return (
    <div className="flex flex-col gap-6">
      <PageHeader eyebrow="Storefront" title="That page doesn't exist" />
      <Card className="flex flex-col items-start gap-3 p-8">
        <p className="max-w-prose text-sm text-muted">
          Nothing lives at this address. The theme gallery is the start of everything in this
          product; the store preview is one click from there.
        </p>
        <div className="flex flex-wrap gap-2">
          <Link
            to="/"
            className="inline-flex h-11 items-center rounded-full bg-accent px-5 font-semibold text-white transition-colors hover:bg-accent-strong"
          >
            Back to themes
          </Link>
          <Link
            to="/store"
            className="inline-flex h-11 items-center rounded-full border border-line bg-surface px-5 font-semibold text-ink transition-colors hover:bg-ground"
          >
            View the store
          </Link>
        </div>
      </Card>
    </div>
  );
}
