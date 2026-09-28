import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { getCountSession, listCountLines } from "@/api/client";
import type { CountLineOut } from "@/api/types";
import { problemMessage, useAsync } from "@opsgrids/client";
import { ArrowRightIcon, Button, Card, CheckCircleIcon, EmptyState, ErrorState, PageHeader, Pill, Skeleton } from "@opsgrids/ui";

function BackLink() {
  return (
    <Link to="/counts" className="inline-flex w-fit items-center gap-2 text-sm font-semibold text-muted">
      <ArrowRightIcon className="size-4 rotate-180" />
      Back to counts
    </Link>
  );
}

type Filter = "todo" | "done" | "all";

/**
 * One count session's lines (ADR-0026, Fulfilment plan F8) — defaults to what still needs
 * counting, the same "the honest starting state" convention `TaskListPanel` sets. Tapping an
 * uncounted line opens the scan/manual entry screen; an already-counted line is read-only here —
 * a mistaken entry is corrected by a manager rejecting it on review, never re-counted by the same
 * worker on this screen.
 */
export function CountSessionLinesPage() {
  const { sessionId = "" } = useParams();
  const navigate = useNavigate();
  const [filter, setFilter] = useState<Filter>("todo");

  const sessionState = useAsync(() => getCountSession(sessionId), [sessionId]);
  const linesState = useAsync(() => listCountLines(sessionId), [sessionId]);

  const lines: CountLineOut[] =
    linesState.status === "success"
      ? linesState.data.items.filter((l) => {
          if (filter === "todo") return l.counted === null;
          if (filter === "done") return l.counted !== null;
          return true;
        })
      : [];

  return (
    <div className="rise flex flex-col gap-4">
      <BackLink />
      <PageHeader
        title={sessionState.status === "success" ? sessionState.data.label || "Stocktake" : "Stocktake"}
        description="Tap a line to count it."
      />

      <div className="flex items-center gap-2">
        {(["todo", "done", "all"] as const).map((f) => (
          <Pill key={f} active={filter === f} onClick={() => setFilter(f)}>
            {f === "todo" ? "To count" : f === "done" ? "Counted" : "All"}
          </Pill>
        ))}
      </div>

      {linesState.status === "loading" ? (
        <div className="flex flex-col gap-3">
          {[0, 1, 2].map((row) => (
            <Skeleton key={row} className="h-16 w-full rounded-tile" />
          ))}
        </div>
      ) : null}

      {linesState.status === "error" ? (
        <Card className="p-2">
          <ErrorState title="Couldn't load lines" message={problemMessage(linesState.error)} onRetry={linesState.reload} />
        </Card>
      ) : null}

      {linesState.status === "success" && lines.length === 0 ? (
        <Card className="p-2">
          <EmptyState
            icon={<CheckCircleIcon className="size-6" aria-hidden />}
            title={filter === "todo" ? "Nothing left to count" : "No lines here"}
            description={filter === "todo" ? "Every line in this session has been counted." : "Try a different filter."}
          />
        </Card>
      ) : null}

      {linesState.status === "success"
        ? lines.map((line) => (
            <Card key={line.id} className="flex items-center justify-between gap-4 p-4">
              <div className="flex min-w-0 flex-col gap-1">
                <p className="truncate text-base font-bold text-ink">{line.sku || "Item"}</p>
                <p className="text-sm text-muted">Expected {line.expected}</p>
                {line.counted !== null ? <p className="text-sm text-present">Counted {line.counted}</p> : null}
              </div>
              {line.counted === null ? (
                <Button size="sm" onClick={() => navigate(`/counts/${sessionId}/lines/${line.id}`)}>
                  Count
                </Button>
              ) : null}
            </Card>
          ))
        : null}
    </div>
  );
}
