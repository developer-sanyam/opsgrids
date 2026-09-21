import { Link } from "react-router-dom";

import { Card } from "@/ui";

/** The 404 for any path outside this app's routes. Its own copy — not the "Coming soon" eyebrow a
 *  placeholder screen would carry, since a page that doesn't exist is a different fact from a
 *  feature that isn't built yet. */
export function NotFoundPage() {
  return (
    <div className="rise flex justify-center pt-8">
      <Card className="w-full max-w-lg p-10 text-center flex flex-col items-center gap-5">
        <div className="flex flex-col gap-2">
          <span className="text-sm font-semibold uppercase tracking-wide text-muted">
            Not found
          </span>
          <h1 className="display text-3xl font-extrabold">That page doesn&apos;t exist</h1>
          <p className="text-sm text-muted leading-relaxed max-w-sm mx-auto">
            Check the address, or head back to the overview.
          </p>
        </div>
        <Link
          to="/"
          className="rounded-full bg-accent px-5 h-11 inline-flex items-center font-semibold text-white hover:bg-accent-strong transition-colors"
        >
          Back to overview
        </Link>
      </Card>
    </div>
  );
}
