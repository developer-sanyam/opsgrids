import { useCallback, useState } from "react";
import { Link } from "react-router-dom";

import { useAsync } from "@opsgrids/client";
import { ActivityIcon } from "@opsgrids/ui";

import { listRuns } from "@/api/automations";
import type { RunOut, RunState } from "@/api/automations";
import { formatInstant } from "@/lib/format";
import { Card, EmptyState, Pagination, Select, Skeleton } from "@/ui";
import type { SelectOption } from "@/ui";
import { RUN_STATES, runStateLabel } from "./labels";
import { RunStateTag } from "./Tags";
import { LoadError } from "./LoadError";

/**
 * The runs of one automation, newest first. A run is one event that started the flow; its subject
 * arrives masked from the service, and it is shown as it arrives. A failed run says why in the
 * service's own machine-readable code, and opens to the step that failed.
 */

const STATE_OPTIONS: SelectOption[] = [
  { value: "", label: "All states" },
  ...RUN_STATES.map((state) => ({ value: state, label: runStateLabel(state) })),
];

const NO_RUNS: RunOut[] = [];

export function RunList({ automationId, published }: { automationId: string; published: boolean }) {
  const [state, setState] = useState<RunState | "">("");
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(25);

  const loader = useCallback(
    () => listRuns(automationId, { ...(state === "" ? {} : { state }), page, size }),
    [automationId, state, page, size],
  );
  const result = useAsync(loader, [automationId, state, page, size]);
  const runs = result.status === "success" ? result.data.items : NO_RUNS;

  return (
    <div className="flex flex-col gap-4">
      <Select
        label="Filter runs by state"
        containerClassName="sm:max-w-xs"
        value={state}
        options={STATE_OPTIONS}
        onChange={(event) => {
          setState(event.target.value as RunState | "");
          setPage(1);
        }}
      />

      {result.status === "loading" ? (
        <Card className="flex flex-col gap-4 p-6" aria-busy="true">
          <span className="sr-only" role="status">
            Loading runs
          </span>
          <Skeleton className="h-6 w-48" />
          <Skeleton className="h-32 w-full rounded-tile" />
        </Card>
      ) : null}

      {result.status === "error" ? (
        <LoadError error={result.error} title="Couldn't load the runs" onRetry={result.reload} />
      ) : null}

      {result.status === "success" && runs.length === 0 ? (
        <Card className="p-6">
          <EmptyState
            icon={<ActivityIcon className="size-6" />}
            title={state === "" ? "No runs yet" : "No runs in that state"}
            description={
              state !== ""
                ? "Choose another state to see the runs in it."
                : published
                  ? "This automation hasn't been triggered yet. A run appears here each time its event happens."
                  : "Publish this automation and its runs appear here each time its event happens."
            }
          />
        </Card>
      ) : null}

      {result.status === "success" && runs.length > 0 ? (
        <Card className="p-6">
          <div className="overflow-x-auto">
            <table className="w-full min-w-3xl border-collapse text-left">
              <caption className="sr-only">Runs of this automation, newest first</caption>
              <thead>
                <tr className="text-xs font-bold uppercase tracking-wider text-faint">
                  <th scope="col" className="pb-2 pr-4">Started</th>
                  <th scope="col" className="pb-2 pr-4">Subject</th>
                  <th scope="col" className="pb-2 pr-4">State</th>
                  <th scope="col" className="pb-2 pr-4">Finished</th>
                  <th scope="col" className="pb-2">Detail</th>
                </tr>
              </thead>
              <tbody>
                {runs.map((run) => (
                  <tr key={run.id} className="border-t border-line align-top">
                    <th scope="row" className="py-3 pr-4 text-left">
                      <Link
                        to={`/automations/${encodeURIComponent(automationId)}/runs/${encodeURIComponent(run.id)}`}
                        className="text-sm font-bold text-ink underline-offset-4 hover:text-accent-ink hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                      >
                        {formatInstant(run.started_at)}
                      </Link>
                      <span className="mt-1 block text-xs text-muted">Version {run.version}</span>
                    </th>
                    <td className="py-3 pr-4 text-sm text-ink">{run.subject}</td>
                    <td className="py-3 pr-4">
                      <RunStateTag state={run.state} />
                    </td>
                    <td className="py-3 pr-4 text-sm text-muted">
                      {run.finished_at === null ? "—" : formatInstant(run.finished_at)}
                    </td>
                    <td className="py-3 text-sm text-muted">
                      {run.failure_code !== null
                        ? run.failure_code
                        : run.next_wake_at !== null
                          ? `Resumes ${formatInstant(run.next_wake_at)}`
                          : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pagination
            page={result.data.page}
            pages={result.data.pages}
            total={result.data.total}
            size={result.data.size}
            noun="runs"
            onPageChange={setPage}
            onSizeChange={(n) => {
              setSize(n);
              setPage(1);
            }}
            className="mt-4"
          />
        </Card>
      ) : null}
    </div>
  );
}
