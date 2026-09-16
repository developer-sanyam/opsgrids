import { useCallback, useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { ApiError } from "@opsgrids/client";
import {
  AlertTriangleIcon,
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
  formatInstant,
  formatRelative,
  useAnnounce,
} from "@opsgrids/ui";
import type { Column } from "@opsgrids/ui";

import { listInbox } from "@/api/approvals";
import type { InboxItemOut } from "@/api/types";
import { dueCellCopy, inboxReasonChip, isOverdue, summaryLine } from "@/lib/approvalCopy";
import { useZoneLookup } from "@/lib/zone";
import { PersonRefBlock } from "@/ui/PersonRef";

const PAGE_SIZE = 25;

// Slice 4 (§4.A): the Due column, its overdue treatment and the `Overdue` filter are built below,
// against `InboxItemOut.due_at` — the server's own value for the step in front of the reader now
// (for an escalated row, the successor's, not the closed predecessor's). The client formats it and
// compares it to the clock; it never derives one, and a `null` is rendered as the modelled "no
// deadline" state it is (§2.11).
//
// Two things §4.A names that are still NOT built, both for want of a server fact rather than a
// design choice: the **overdue banner** ("{n} requests are overdue.") needs an `overdue_count` over
// the whole result set, and `platform_core.pagination.Page` carries no such field — counting the
// loaded pages would report "3 requests are overdue" to someone with 300 unloaded rows; and the
// **Due sort** is specified as server-side (`nulls last`), while `GET /inbox` accepts only
// `page`/`size`. Both are reported rather than approximated.
type Filter = "all" | "assigned" | "unassigned" | "escalated_to_me" | "overdue";

type LoadState =
  | { status: "loading" }
  | { status: "error"; error: Error }
  | { status: "success" };

function ApprovalsInbox() {
  const navigate = useNavigate();
  const location = useLocation();
  const announce = useAnnounce();
  const zoneLookup = useZoneLookup();

  const [items, setItems] = useState<InboxItemOut[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loadingMore, setLoadingMore] = useState(false);
  const [state, setState] = useState<LoadState>({ status: "loading" });
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [everSawUnassigned, setEverSawUnassigned] = useState(false);
  // One clock reading per load, shared by the Overdue filter and every Due cell, so a row can
  // never read "Due in a minute" while the filter has already counted it as overdue. It is
  // re-read whenever the list is, which is also when a `due_at` could have changed.
  const [now, setNow] = useState(() => new Date());

  const load = useCallback(async (pageToLoad: number, append: boolean) => {
    if (append) setLoadingMore(true);
    else setState({ status: "loading" });
    try {
      const result = await listInbox({ page: pageToLoad, size: PAGE_SIZE });
      setItems((prev) => (append ? [...prev, ...result.items] : result.items));
      setTotal(result.total);
      setPage(pageToLoad);
      setNow(new Date());
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
        (filter === "unassigned" && item.inbox_reason === "unassigned") ||
        (filter === "escalated_to_me" && item.inbox_reason === "escalated_to_me") ||
        // §4.A: `due_at < now`, off the server's value. A row with no deadline is never overdue.
        (filter === "overdue" && isOverdue(item.due_at, now));
      const matchesSearch =
        q === "" ||
        (item.requester_name?.toLowerCase().includes(q) ?? false) ||
        (item.request_note?.toLowerCase().includes(q) ?? false);
      return matchesFilter && matchesSearch;
    });
  }, [items, search, filter, now]);

  const hasActiveFilter = filter !== "all" || search.trim() !== "";

  /**
   * §2.1: an instant on an approval row renders in the **requester's** location zone, resolved
   * from the row's own `requester_location_code` against the cached location list — never the
   * browser's. The one-per-session lookup may not have resolved on first paint, and a zone fact
   * this screen does not have yet is one it does not assert: the tooltip is absent until it
   * arrives. Nothing visible depends on it, so nothing moves when it does.
   */
  const absoluteAt = (row: InboxItemOut, iso: string): string | undefined =>
    zoneLookup
      ? formatInstant(iso, row.requester_location_code, zoneLookup, "datetime", now).text
      : undefined;

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
        <span className="text-sm text-muted" title={absoluteAt(r, r.opened_at)}>
          {formatRelative(r.opened_at, now)}
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
    {
      // §4.A: the Due column stays visible at every width — it is the other half of "should I act
      // on this now". The relative duration is the primary read (it needs no zone at all); the
      // absolute instant, in the requester's own zone, rides in the `title`.
      key: "due",
      header: "Due",
      render: (r) => {
        const due = dueCellCopy(r.due_at, now);
        if (due.kind === "none") {
          // §2.11: `null` means *there is no deadline* — said in words, never left blank, never
          // decorated as an error, and never replaced with a computed default.
          return <span className="text-sm text-muted">{due.label}</span>;
        }
        return (
          <span
            className={`inline-flex items-center gap-1.5 text-sm whitespace-nowrap ${
              due.kind === "overdue" ? "text-negative font-semibold" : "text-muted"
            }`}
            title={r.due_at ? absoluteAt(r, r.due_at) : undefined}
          >
            {/* Never colour alone (§4.A): the words "Overdue by" carry the state, the icon
             *  reinforces it, and both are inside the row's accessible name. */}
            {due.kind === "overdue" ? (
              <AlertTriangleIcon className="size-4 shrink-0" aria-hidden />
            ) : null}
            {due.label}
          </span>
        );
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
          {/* §4.A slice 4: the two filters that ship with the SLA sweep. `Escalated to me` reads
           *  `inbox_reason`, which has shipped since slice 2; `Overdue` reads `due_at`, which
           *  ships now. Both render unconditionally — unlike `Unassigned`, neither doubles as a
           *  permission tell, so an empty result is a true answer rather than a leak. */}
          <Pill
            active={filter === "escalated_to_me"}
            onClick={() => setFilter("escalated_to_me")}
          >
            Escalated to me
          </Pill>
          <Pill active={filter === "overdue"} onClick={() => setFilter("overdue")}>
            Overdue
          </Pill>
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
