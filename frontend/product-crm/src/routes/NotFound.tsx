import { Link } from "react-router-dom";

import { Card } from "@/ui";

/**
 * The route table's catch-all: an address nothing serves. It sits inside the signed-in shell, so
 * the way back is the overview. Nothing loads here, so it has no loading, empty, error or
 * forbidden state — a dead link is the whole of its job.
 */
export function NotFoundPage() {
  return (
    <div className="rise flex justify-center pt-8">
      <Card className="flex w-full max-w-lg flex-col items-center gap-4 p-8 text-center">
        <div className="flex flex-col gap-2">
          <span className="text-sm font-semibold uppercase tracking-wide text-muted">Not found</span>
          <h1 className="display text-3xl font-extrabold">That page doesn't exist</h1>
          <p className="mx-auto max-w-sm text-sm leading-relaxed text-muted">
            Check the address, or head back to the overview.
          </p>
        </div>
        <Link
          to="/"
          className="inline-flex h-11 items-center rounded-full bg-accent px-6 font-semibold text-on-accent transition-colors hover:bg-accent-strong"
        >
          Back to overview
        </Link>
      </Card>
    </div>
  );
}
