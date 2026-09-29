import { Link } from "react-router-dom";

import { listActiveCountSessions } from "@/api/client";
import { problemMessage, useAsync } from "@opsgrids/client";
import { Card, CheckSquareIcon, EmptyState, ErrorState, PageHeader, Skeleton } from "@opsgrids/ui";

/**
 * The worker's landing screen for a stocktake (ADR-0026, Fulfilment plan F8): every count session
 * currently `ACTIVE` anywhere in the org — in practice at most one per warehouse at a time
 * (ADR-0026 §5) — tap one to start counting its lines. Opening, reviewing, closing and approving
 * discrepancies are the manager's job (`frontend/product-operations`), not this screen's.
 */
export function CountsPage() {
  const state = useAsync(() => listActiveCountSessions(), []);

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader title="Counts" description="Active stocktakes you can count against." />

      {state.status === "loading" ? (
        <div className="flex flex-col gap-3">
          {[0, 1].map((row) => (
            <Skeleton key={row} className="h-20 w-full rounded-tile" />
          ))}
        </div>
      ) : null}

      {state.status === "error" ? (
        <Card className="p-2">
          <ErrorState title="Couldn't load counts" message={problemMessage(state.error)} onRetry={state.reload} />
        </Card>
      ) : null}

      {state.status === "success" && state.data.items.length === 0 ? (
        <Card className="p-2">
          <EmptyState
            icon={<CheckSquareIcon className="size-6" aria-hidden />}
            title="No active counts"
            description="A stocktake will appear here once a manager opens one for your warehouse."
          />
        </Card>
      ) : null}

      {state.status === "success" && state.data.items.length > 0
        ? state.data.items.map((session) => (
            <Link key={session.id} to={`/counts/${session.id}`}>
              <Card className="flex items-center justify-between gap-4 p-4">
                <div className="flex min-w-0 flex-col gap-1">
                  <p className="truncate text-base font-bold text-ink">{session.label || "Stocktake"}</p>
                  <p className="text-sm text-muted">
                    {session.counted_count}/{session.line_count} lines counted
                  </p>
                </div>
                <span className="tnum text-sm font-semibold text-muted">{session.scope_kind}</span>
              </Card>
            </Link>
          ))
        : null}
    </div>
  );
}
