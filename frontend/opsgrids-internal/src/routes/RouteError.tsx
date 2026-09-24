import { isRouteErrorResponse, Link, useRouteError } from "react-router-dom";

import { ApiError } from "@/api/http";
import { Card } from "@/ui";
import { AlertTriangleIcon } from "@/ui/icons";

/** Turn whatever React Router (or a component render) threw into a message worth showing an
 *  operator — never the raw stack, but never a lie about what happened either. */
function messageFor(error: unknown): string {
  if (isRouteErrorResponse(error)) {
    return error.data && typeof error.data === "string" ? error.data : `Request failed with ${error.status}`;
  }
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return "Something went wrong rendering this page.";
}

/**
 * The route-level error boundary: one bad response or a render bug on a single page shows a
 * contained card in the content column, instead of the whole app going blank behind React
 * Router's own "Unexpected Application Error!" (the production bug this was built to stop —
 * `src/routes/Overview.tsx` reading a shape the live API doesn't return, uncaught, took out every
 * screen behind the same router because no route declared an `errorElement`).
 *
 * Every page and every operator action shown here (Overview, Accounts, an account's detail,
 * Subscriptions, Plans, Grants, Audit) is wired to this per-route, so a mistake on one screen never
 * takes the sidebar, the other screens or the session down with it.
 */
export function RouteErrorCard() {
  const error = useRouteError();
  const message = messageFor(error);

  return (
    <div className="rise flex justify-center pt-8">
      <Card className="flex w-full max-w-lg flex-col items-center gap-5 p-10 text-center" role="alert">
        <span className="grid size-14 place-items-center rounded-full bg-danger-soft text-danger">
          <AlertTriangleIcon className="size-6" />
        </span>
        <div className="flex flex-col gap-2">
          <h1 className="display text-xl font-extrabold">This page couldn't load</h1>
          <p className="mx-auto max-w-sm text-sm text-muted">{message}</p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="inline-flex h-10 items-center rounded-full bg-ink px-4 text-sm font-semibold text-white hover:bg-ink-soft transition-colors"
          >
            Try again
          </button>
          <Link
            to="/"
            className="inline-flex h-10 items-center rounded-full border border-line px-4 text-sm font-semibold text-ink-soft hover:bg-ground transition-colors"
          >
            Back to overview
          </Link>
        </div>
      </Card>
    </div>
  );
}
