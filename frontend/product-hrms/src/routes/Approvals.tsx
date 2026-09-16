import { useCallback, useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { ApiError } from "@opsgrids/client";
import {
  Announcer,
  Button,
  Card,
  DataTable,
  EmptyState,
  ErrorState,
  InboxIcon,
  LoadMore,
  PageHeader,
  Pill,
  SearchInput,
  Skeleton,
  Tag,
  formatRelative,
  useAnnounce,
} from "@opsgrids/ui";
import type { Column } from "@opsgrids/ui";

import { listInbox } from "@/api/approvals";
import type { InboxItemOut } from "@/api/types";
import { inboxReasonChip, summaryLine } from "@/lib/approvalCopy";
import { PersonRefBlock } from "@/ui/PersonRef";

const PAGE_SIZE = 25;

type Filter = "all" | "assigned" | "unassigned";

type LoadState =
  | { status: "loading" }
  | { status: "error"; error: Error }
  | { status: "success" };

function ApprovalsInbox() {
  const navigate = useNavigate();
  const location = useLocation();
  const announce = useAnnounce();

  const [items, setItems] = useState<InboxItemOut[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loadingMore, setLoadingMore] = useState(false);
  const [state, setState] = useState<LoadState>({ status: "loading" });
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [everSawUnassigned, setEverSawUnassigned] = useState(false);

  const load = useCallback(async (pageToLoad: number, append: boolean) => {
    if (append) setLoadingMore(true);
    else setState({ status: "loading" });
    try {
      const result = await listInbox({ page: pageToLoad, size: PAGE_SIZE });
      setItems((prev) => (append ? [...prev, ...result.items] : result.items));
      setTotal(result.total);
      setPage(pageToLoad);
      setState({ status: "success" });
      if (result.items.some((i) => i.inbox_reason === "unassigned")) setEverSawUnassigned(true);
    } catch (err) {
      setState({ status: "error", error: err instanceof Error ? err : new Error(String(err)) });
    } finally {
      setLoadingMore(false);
    }
  }, []);

  useEffect(() => {
    void load(1, false);
    // Loaded once — the backend accepts no filter/search/sort params (a known gap against the
    // spec; see the slice-2 report), so re-filtering happens client-side over what's loaded.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const state = location.state as { announce?: string } | null;
    if (state?.announce) {
      announce(state.announce);
      // Consume the message so a later back/forward doesn't replay it.
      navigate(location.pathname, { replace: true, state: null });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.state]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return items.filter((item) => {
      const matchesFilter =
        filter === "all" ||
        (filter === "assigned" && item.inbox_reason === "assigned") ||
        (filter === "unassigned" && item.inbox_reason === "unassigned");
      const matchesSearch =
        q === "" ||
        (item.requester_name?.toLowerCase().includes(q) ?? false) ||
        (item.request_note?.toLowerCase().includes(q) ?? false);
      return matchesFilter && matchesSearch;
    });
  }, [items, search, filter]);

  const hasActiveFilter = filter !== "all" || search.trim() !== "";

  const columns: Column<InboxItemOut>[] = [
    {
      key: "requester",
      header: "Requester",
      render: (r) => {
        const chip = inboxReasonChip(r.inbox_reason, r.from_name);
        return (
          <div className="flex flex-col gap-1">
            <PersonRefBlock name={r.requester_name} employeeNo={r.requester_employee_no} />
            {/* §4.A: "The `Why` chip moves under the requester name at `< 768` so the reason for
             *  possession is never lost — it is the field that tells an approver whether to act
             *  at all." Below `md` this is the only place the chip renders; the dedicated `Why`
             *  column (below) stays hidden there. */}
            {chip ? (
              <div className="md:hidden">
                <Tag tone={chip.tone}>{chip.label}</Tag>
              </div>
            ) : null}
          </div>
        );
      },
    },
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
      key: "reason",
      header: "Reason",
      cellClassName: "hidden lg:table-cell max-w-64",
      headerClassName: "hidden lg:table-cell",
      render: (r) =>
        r.request_note ? (
          <span className="block truncate text-sm text-ink-soft" title={r.request_note}>
            {r.request_note}
          </span>
        ) : null,
    },
    {
      key: "waiting_since",
      header: "Waiting since",
      cellClassName: "hidden sm:table-cell",
      headerClassName: "hidden sm:table-cell",
      render: (r) => (
        <span className="text-sm text-muted" title={r.opened_at}>
          {formatRelative(r.opened_at)}
        </span>
      ),
    },
    {
      key: "why",
      header: "Why",
      cellClassName: "hidden md:table-cell",
      headerClassName: "hidden md:table-cell",
      render: (r) => {
        const chip = inboxReasonChip(r.inbox_reason, r.from_name);
        return chip ? <Tag tone={chip.tone}>{chip.label}</Tag> : null;
      },
    },
  ];

  if (state.status === "error" && state.error instanceof ApiError && state.error.status === 403) {
    return (
      <ErrorState
        title="Approvals aren't switched on here yet"
        message="Your role doesn't include approvals. An owner can check with OpsGrids support."
        tone="neutral"
      />
    );
  }

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Waiting on you"
        title="Approvals"
        actions={
          <span className="rounded-full bg-surface shadow-card px-4 h-11 inline-flex items-center gap-2 text-sm font-semibold">
            <InboxIcon className="size-4 text-muted" />
            <span className="tnum">{total}</span>
            <span className="text-muted font-medium">waiting</span>
          </span>
        }
      />

      <Card className="p-4 sm:p-5 flex flex-col gap-4">
        <SearchInput
          label="Search requests"
          placeholder="Search by person or reason…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          containerClassName="lg:max-w-xs"
        />
        <div className="flex items-center gap-1.5 overflow-x-auto -mx-1 px-1">
          <Pill active={filter === "all"} onClick={() => setFilter("all")}>
            All
          </Pill>
          <Pill active={filter === "assigned"} onClick={() => setFilter("assigned")}>
            Assigned to me
          </Pill>
          {everSawUnassigned ? (
            <Pill active={filter === "unassigned"} onClick={() => setFilter("unassigned")}>
              Unassigned
            </Pill>
          ) : null}
        </div>
      </Card>

      {state.status === "loading" ? (
        <Card className="p-6">
          <div className="flex flex-col gap-3">
            {Array.from({ length: 8 }).map((_, i) => (
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
            caption="Requests waiting on you"
            columns={columns}
            rows={filtered}
            rowKey={(r) => r.id}
            onRowClick={(r) => navigate(`/approvals/requests/${r.id}`)}
            emptyState={
              items.length === 0 ? (
                <EmptyState
                  icon={<InboxIcon className="size-6" />}
                  title="Nothing is waiting on you"
                  description="When someone asks you to approve something, it lands here. You'll also see anything delegated or escalated to you."
                />
              ) : (
                <EmptyState
                  title="No requests match this filter"
                  description="Try another filter or clear the search."
                  action={
                    <Button
                      variant="outline"
                      onClick={() => {
                        setSearch("");
                        setFilter("all");
                      }}
                    >
                      Clear filters
                    </Button>
                  }
                />
              )
            }
          />
          {items.length > 0 ? (
            // Design QA: the backend accepts no server-side search/filter params (the known gap
            // noted above `listInbox`), so `filtered` is a client-side view over `items` while
            // "Load more · {n} left" is necessarily computed from the server's unfiltered
            // `total` — the two numbers answer different questions. Loading another page would
            // add rows the active filter or search may drop entirely, so the count would be
            // promising rows that never appear. Chosen fix: disable "Load more" (replace it with
            // an explanatory line) whenever a filter or search is active and there is more to
            // load; once every row is already loaded, the closing "Showing all N" line is true
            // regardless of what's filtered, so it renders normally.
            hasActiveFilter && items.length < total ? (
              <p className="text-center text-sm text-muted">
                Search and filters only look at what's loaded so far. Clear them to load more
                requests.
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

export function ApprovalsPage() {
  return (
    <Announcer>
      <ApprovalsInbox />
    </Announcer>
  );
}
