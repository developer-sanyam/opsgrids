import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { listJobWorkOrders } from "@/api/client";
import type { JobWorkOrderOut, JobWorkStatus } from "@/api/types";
import { problemMessage, useAsync, useCan } from "@opsgrids/client";
import { JobWorkOrderFormDialog } from "@/forms/JobWorkOrderFormDialog";
import { formatDate } from "@/lib/format";
import { formatQty } from "@/lib/quantity";
import {
  Button,
  Card,
  DataTable,
  EmptyState,
  ErrorState,
  JobWorkStatusPill,
  OverduePill,
  PageHeader,
  Pagination,
  Pill,
  SearchInput,
  Skeleton,
} from "@/ui";
import type { Column } from "@/ui";
import { PlusIcon, ReceiptIcon, useDebouncedValue } from "@opsgrids/ui";

type StatusFilter = JobWorkStatus | "all";

const STATUS_FILTERS: { value: StatusFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "DRAFT", label: "Draft" },
  { value: "APPROVED", label: "Approved" },
  { value: "ISSUED", label: "At vendor" },
  { value: "PARTIALLY_RECEIVED", label: "Partly received" },
  { value: "RECEIVED", label: "Received" },
  { value: "CLOSED", label: "Closed" },
  { value: "CANCELLED", label: "Cancelled" },
];

/**
 * Job work (Inventory plan Phase 6) — material sent to an outside processor and expected back.
 * Every figure here is the server's: nothing is re-filtered, re-summed or capped client-side, and
 * quantities are shown as the decimal strings they arrive as. "Overdue" is a filter on the server
 * (due date passed while material is still at the vendor), not a status.
 */
export function JobWorkOrdersPage() {
  const navigate = useNavigate();
  const canWrite = useCan("operations.jobwork.order.write", "ORG");
  const [status, setStatus] = useState<StatusFilter>("all");
  const [overdue, setOverdue] = useState(false);
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebouncedValue(search);
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(25);
  const [creating, setCreating] = useState(false);

  const state = useAsync(
    () => listJobWorkOrders({ status, overdue, search: debouncedSearch, page, size }),
    [status, overdue, debouncedSearch, page, size],
  );

  const columns: Column<JobWorkOrderOut>[] = [
    {
      key: "order",
      header: "Order",
      mobile: "title",
      render: (o) => (
        <div className="flex flex-col leading-tight">
          <Link to={`/jobwork/orders/${o.id}`} className="font-semibold text-ink hover:underline">
            {o.number}
          </Link>
          <span className="text-xs text-muted">{o.process || "—"}</span>
        </div>
      ),
    },
    { key: "vendor", header: "Vendor", render: (o) => <span className="text-ink">{o.supplier_name || "—"}</span> },
    {
      key: "status",
      header: "Status",
      mobile: "status",
      render: (o) => (
        <div className="flex flex-wrap items-center gap-2">
          <JobWorkStatusPill status={o.status} />
          {o.is_overdue ? <OverduePill days={o.days_overdue} /> : null}
        </div>
      ),
    },
    {
      key: "due",
      header: "Due back",
      cellClassName: "hidden sm:table-cell",
      headerClassName: "hidden sm:table-cell",
      render: (o) => <span className="text-muted">{formatDate(o.due_date)}</span>,
    },
    {
      key: "sent",
      header: "Sent",
      mobile: "hidden",
      align: "right",
      cellClassName: "hidden md:table-cell",
      headerClassName: "hidden md:table-cell",
      render: (o) => <span className="tnum text-muted">{formatQty(o.total_sent)}</span>,
    },
    {
      key: "outstanding",
      header: "At vendor",
      align: "right",
      render: (o) => <span className="tnum text-ink">{formatQty(o.total_outstanding)}</span>,
    },
  ];

  const filtersActive = status !== "all" || overdue || debouncedSearch !== "";

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Job work"
        title="Job-work orders"
        description="Material sent to a dyer, stitcher or finisher — and what has come back."
        actions={
          canWrite ? (
            <Button onClick={() => setCreating(true)}>
              <PlusIcon className="size-4" />
              New order
            </Button>
          ) : undefined
        }
      />

      <Card className="p-4 sm:p-6 flex flex-col gap-4">
        <SearchInput
          label="Search job-work orders"
          placeholder="Search by order number…"
          value={search}
          onChange={(e) => {
            setSearch(e.target.value);
            setPage(1);
          }}
          containerClassName="lg:max-w-sm"
        />
        <div className="flex items-center gap-2 overflow-x-auto -mx-1 px-1">
          {STATUS_FILTERS.map((f) => (
            <Pill
              key={f.value}
              active={status === f.value}
              onClick={() => {
                setStatus(f.value);
                setPage(1);
              }}
            >
              {f.label}
            </Pill>
          ))}
          <Pill
            active={overdue}
            onClick={() => {
              setOverdue((v) => !v);
              setPage(1);
            }}
          >
            Overdue only
          </Pill>
        </div>
      </Card>

      <Card className="p-2 sm:p-4">
        {state.status === "loading" ? (
          <div className="flex flex-col gap-3 p-3" aria-busy="true" aria-label="Loading job-work orders">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full rounded-tile" />
            ))}
          </div>
        ) : null}
        {state.status === "error" ? (
          <ErrorState
            title="Couldn't load job-work orders"
            message={problemMessage(state.error)}
            onRetry={state.reload}
          />
        ) : null}
        {state.status === "success" ? (
          <>
            <DataTable
              columns={columns}
              rows={state.data.items}
              rowKey={(o) => o.id}
              caption="Job-work orders"
              emptyState={
                <EmptyState
                  icon={<ReceiptIcon className="size-6" />}
                  title={filtersActive ? "No orders match these filters" : "No job-work orders yet"}
                  description={
                    filtersActive
                      ? "Try a different status, or clear the search."
                      : canWrite
                        ? "Draft one to send material to a vendor."
                        : "Orders appear here once someone drafts them."
                  }
                />
              }
            />
            <Pagination
              page={state.data.page}
              pages={state.data.pages}
              total={state.data.total}
              size={state.data.size}
              noun="orders"
              onPageChange={setPage}
              onSizeChange={(n) => {
                setSize(n);
                setPage(1);
              }}
            />
          </>
        ) : null}
      </Card>

      {creating && canWrite ? (
        <JobWorkOrderFormDialog
          onClose={() => setCreating(false)}
          onCreated={(order) => {
            setCreating(false);
            navigate(`/jobwork/orders/${order.id}`);
          }}
        />
      ) : null}
    </div>
  );
}
