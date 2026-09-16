import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import {
  Announcer,
  Button,
  Card,
  ClipboardListIcon,
  DataTable,
  EmptyState,
  ErrorState,
  LoadMore,
  PageHeader,
  Pill,
  Skeleton,
  Tag,
  formatInstant,
} from "@opsgrids/ui";
import type { Column } from "@opsgrids/ui";

import { listMyRequests } from "@/api/approvals";
import type { RequestListItemOut } from "@/api/types";
import { myRequestStatusCopy, summaryLine } from "@/lib/approvalCopy";
import { useZoneLookup } from "@/lib/zone";
import { PersonRefInline } from "@/ui/PersonRef";

const PAGE_SIZE = 25;

type Filter = "all" | "pending" | "approved" | "rejected";

type LoadState = { status: "loading" } | { status: "error"; error: Error } | { status: "success" };

function MyRequestsList() {
  const navigate = useNavigate();
  const zoneLookup = useZoneLookup();

  const [items, setItems] = useState<RequestListItemOut[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loadingMore, setLoadingMore] = useState(false);
  const [state, setState] = useState<LoadState>({ status: "loading" });
  const [filter, setFilter] = useState<Filter>("all");

  const load = useCallback(async (pageToLoad: number, append: boolean) => {
    if (append) setLoadingMore(true);
    else setState({ status: "loading" });
    try {
      const result = await listMyRequests({ page: pageToLoad, size: PAGE_SIZE });
      setItems((prev) => (append ? [...prev, ...result.items] : result.items));
      setTotal(result.total);
      setPage(pageToLoad);
      setState({ status: "success" });
    } catch (err) {
      setState({ status: "error", error: err instanceof Error ? err : new Error(String(err)) });
    } finally {
      setLoadingMore(false);
    }
  }, []);

  useEffect(() => {
    void load(1, false);
  }, [load]);

  const filtered = items.filter((item) => filter === "all" || item.status === filter);
  const hasActiveFilter = filter !== "all";

  const columns: Column<RequestListItemOut>[] = [
    {
      key: "request",
      header: "Request",
      render: (r) => (
        <div className="flex flex-col leading-tight">
          <span className="font-semibold text-ink">Attendance correction</span>
          <span className="text-xs text-muted">{summaryLine(r.summary)}</span>
        </div>
      ),
    },
    {
      key: "status",
      header: "Status",
      render: (r) => {
        const copy = myRequestStatusCopy(r.status, r.application_state, r.approver_name);
        return (
          <span title={r.application_failure_reason ?? undefined}>
            <Tag tone={copy.tone}>{copy.label}</Tag>
          </span>
        );
      },
    },
    {
      key: "raised",
      header: "Raised",
      cellClassName: "hidden sm:table-cell",
      headerClassName: "hidden sm:table-cell",
      render: (r) => (
        <span className="text-sm text-muted tnum">
          {zoneLookup
            ? formatInstant(r.opened_at, r.requester_location_code, zoneLookup).text
            : "…"}
        </span>
      ),
    },
    {
      key: "decided",
      header: "Decided",
      cellClassName: "hidden md:table-cell",
      headerClassName: "hidden md:table-cell",
      render: (r) =>
        r.decided_at && zoneLookup ? (
          <div className="flex items-center gap-2">
            <span className="text-sm text-muted tnum">
              {formatInstant(r.decided_at, r.requester_location_code, zoneLookup).text}
            </span>
            <PersonRefInline name={r.decided_by_name} />
          </div>
        ) : (
          <span className="text-sm text-faint">—</span>
        ),
    },
  ];

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader eyebrow="Raised by you" title="My requests" />

      <Card className="p-4 sm:p-5 flex flex-col gap-4">
        <div className="flex items-center gap-1.5 overflow-x-auto -mx-1 px-1">
          {(
            [
              { value: "all", label: "All" },
              { value: "pending", label: "Waiting" },
              { value: "approved", label: "Approved" },
              { value: "rejected", label: "Rejected" },
            ] as { value: Filter; label: string }[]
          ).map((f) => (
            <Pill key={f.value} active={filter === f.value} onClick={() => setFilter(f.value)}>
              {f.label}
            </Pill>
          ))}
        </div>
      </Card>

      {state.status === "loading" ? (
        <Card className="p-6">
          <div className="flex flex-col gap-3">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-14 w-full rounded-tile" />
            ))}
          </div>
        </Card>
      ) : null}

      {state.status === "error" ? (
        <Card className="p-6">
          <ErrorState message={state.error.message} onRetry={() => void load(1, false)} />
        </Card>
      ) : null}

      {state.status === "success" ? (
        <Card className="p-2 sm:p-4 flex flex-col gap-4">
          <DataTable
            caption="Requests you raised"
            columns={columns}
            rows={filtered}
            rowKey={(r) => r.id}
            onRowClick={(r) => navigate(`/approvals/requests/${r.id}`, { state: { from: "mine" } })}
            emptyState={
              items.length === 0 ? (
                <EmptyState
                  icon={<ClipboardListIcon className="size-6" />}
                  title="You haven't raised any requests"
                  description="If an attendance record is wrong, open Attendance and ask for a correction."
                  action={
                    <Button variant="dark" onClick={() => navigate("/attendance")}>
                      Go to attendance
                    </Button>
                  }
                />
              ) : (
                <EmptyState
                  title="No requests with this status"
                  description="Try another filter."
                  action={
                    <Button variant="outline" onClick={() => setFilter("all")}>
                      Clear filters
                    </Button>
                  }
                />
              )
            }
          />
          {items.length > 0 ? (
            // Design QA (same fix as Approvals.tsx): the status filter is applied client-side
            // over already-loaded pages, so "Load more · {n} left" — computed from the server's
            // unfiltered `total` — can promise rows the active filter would drop. Disabled here
            // (replaced by an explanatory line) while a filter is active and more remains; once
            // every row is loaded the closing "Showing all N" line is accurate regardless.
            hasActiveFilter && items.length < total ? (
              <p className="text-center text-sm text-muted">
                This filter only looks at what's loaded so far. Clear it to load more requests.
              </p>
            ) : (
              <LoadMore
                loaded={items.length}
                total={total}
                noun="requests"
                loading={loadingMore}
                onLoadMore={() => void load(page + 1, true)}
              />
            )
          ) : null}
        </Card>
      ) : null}
    </div>
  );
}

export function MyRequestsPage() {
  return (
    <Announcer>
      <MyRequestsList />
    </Announcer>
  );
}
