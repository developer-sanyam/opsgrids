import { Link } from "react-router-dom";

import { GaugeIcon } from "@opsgrids/ui";

import { Card } from "@/ui";

/** A 404 for an unknown console route. */
export function NotFoundPage() {
  return (
    <div className="rise flex justify-center pt-8">
      <Card className="flex w-full max-w-lg flex-col items-center gap-5 p-10 text-center">
        <span className="grid size-16 place-items-center rounded-full bg-accent-soft text-accent-ink">
          <GaugeIcon className="size-6" />
        </span>
        <div className="flex flex-col gap-2">
          <span className="text-sm font-semibold uppercase tracking-wide text-muted">404</span>
          <h1 className="display text-3xl font-extrabold">Page not found</h1>
          <p className="mx-auto max-w-sm text-sm text-muted">
            That console page doesn't exist. Head back to the overview.
          </p>
        </div>
        <Link
          to="/"
          className="inline-flex h-11 items-center rounded-full bg-accent px-5 font-semibold text-white transition-colors hover:bg-accent-strong"
        >
          Back to overview
        </Link>
      </Card>
    </div>
  );
}
