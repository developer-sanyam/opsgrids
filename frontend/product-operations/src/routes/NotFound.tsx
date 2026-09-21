import { Link } from "react-router-dom";

import { Card } from "@/ui";

/** A real 404 — reached only by navigating to a path nothing in the app links to. */
export function NotFoundPage() {
  return (
    <div className="rise flex justify-center pt-8">
      <Card className="w-full max-w-lg p-10 text-center flex flex-col items-center gap-5">
        <div className="flex flex-col gap-2">
          <span className="text-sm font-semibold uppercase tracking-wide text-muted">
            Not found
          </span>
          <h1 className="display text-3xl font-extrabold">That page doesn't exist</h1>
          <p className="text-sm text-muted leading-relaxed max-w-sm mx-auto">
            The page you were looking for isn't here. Head back to the overview.
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
