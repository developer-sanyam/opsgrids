import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { listTransfers } from "@/api/movements";
import type { TransferStatus, TransferSummaryOut } from "@/api/movementTypes";
import { Can, problemMessage } from "@opsgrids/client";
import { formatDate } from "@/lib/format";
import { InTransitCard } from "@/movements/InTransitCard";
import { LoadMoreFooter } from "@/movements/LoadMoreFooter";
import { useCursorList } from "@/movements/useCursorList";
import {
  ApprovalRouteChip,
  Button,
  Card,
  DataTable,
  EmptyState,
  ErrorState,
  PageHeader,
  Pill,
  SearchInput,
  TransferStatusPill,
} from "@/ui";
import type { Column } from "@/ui";
import { PlusIcon, TruckIcon } from "@opsgrids/ui";

type StatusFilter = TransferStatus | "all";

const STATUS_FILTERS: { value: StatusFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "DRAFT", label: "Draft" },
  { value: "REQUESTED", label: "Requested" },
  { value: "APPROVED", label: "Approved" },
  { value: "IN_TRANSIT", label: "In transit" },
  { value: "RECEIVED", label: "Received" },
  { value: "CANCELLED", label: "Cancelled" },
  { value: "REJECTED", label: "Rejected" },
];

/**
 * Inter-warehouse transfers (inventory Phase 3). A cursor-paged list — newest first, filtered by
 * status and by transfer number — above the report of what is in transit. "New transfer" is
 * offered to holders of `operations.inventory.transfer.write`; the server enforces it regardless.
 */
export function TransfersPage() {
  const navigate = useNavigate();
  const [status, setStatus] = useState<StatusFilter>("all");
  const [search, setSearch] = useState("");

  const list = useCursorList(
    (cursor, signal) =>
      listTransfers({
        status: status === "all" ? undefined : status,
        q: search.trim() === "" ? undefined : search.trim(),
        cursor,
        limit: 25,
        signal,
      }),
    [status, search],
  );

  const filtersActive = status !== "all" || search.trim() !== "";

  const columns: Column<TransferSummaryOut>[] = [
    {
      key: "number",
      header: "Transfer",
      mobile: "title",
      render: (t) => (
        <Link to={`/inventory/transfers/${t.id}`} className="font-semibold text-ink tnum hover:underline">
          {t.number}
        </Link>
      ),
    },
    {
      key: "route",
      header: "From → to",
      render: (t) => (
        <span className="text-ink-soft">
          {t.from_warehouse.code} <span aria-hidden="true">→</span>
          <span className="sr-only">to</span> {t.to_warehouse.code}
        </span>
      ),
    },
    {
      key: "status",
      header: "Status",
      mobile: "status",
      render: (t) => <TransferStatusPill status={t.status} />,
    },
    {
      key: "approval",
      header: "Approval",
      cellClassName: "hidden md:table-cell",
      headerClassName: "hidden md:table-cell",
      render: (t) => <ApprovalRouteChip route={t.approval_route} />,
    },
    {
      key: "lines",
      header: "Lines",
      align: "right",
      cellClassName: "hidden sm:table-cell",
      headerClassName: "hidden sm:table-cell",
      render: (t) => <span className="tnum text-muted">{t.line_count}</span>,
    },
    {
      key: "expected",
      header: "Expected",
      cellClassName: "hidden lg:table-cell",
      headerClassName: "hidden lg:table-cell",
      render: (t) => <span className="text-muted">{formatDate(t.expected_on)}</span>,
    },
    {
      key: "created",
      header: "Created",
      cellClassName: "hidden lg:table-cell",
      headerClassName: "hidden lg:table-cell",
      render: (t) => <span className="text-muted">{formatDate(t.created_at.slice(0, 10))}</span>,
    },
  ];

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Inventory"
        title="Transfers"
        description="Move stock between warehouses. It leaves the source on dispatch and counts as in transit until the destination receives it."
        actions={
          <Can permission="operations.inventory.transfer.write" scope="ORG">
            <Button leadingIcon={<PlusIcon className="size-4" />} onClick={() => navigate("/inventory/transfers/new")}>
              New transfer
            </Button>
          </Can>
        }
      />

      <InTransitCard />

      <Card className="p-4 sm:p-6 flex flex-col gap-4">
        <div className="flex items-center gap-2 overflow-x-auto -mx-1 px-1" role="group" aria-label="Filter by status">
          {STATUS_FILTERS.map((f) => (
            <Pill key={f.value} active={status === f.value} onClick={() => setStatus(f.value)}>
              {f.label}
            </Pill>
          ))}
        </div>
        <SearchInput
          label="Search by transfer number"
          placeholder="Search by number, like TRF-0000…"
          value={search}
          onChange={() => undefined}
          onDebouncedChange={setSearch}
          maxLength={30}
          containerClassName="lg:max-w-sm"
        />
      </Card>

      <Card className="p-2 sm:p-4">
        <DataTable
          columns={columns}
          rows={list.rows}
          rowKey={(t) => t.id}
          caption="Transfers"
          mobileCard="auto"
          loading={list.status === "loading"}
          loadingRows={6}
          errorState={
            list.status === "error" ? (
              <ErrorState title="Couldn't load transfers" message={problemMessage(list.error)} onRetry={list.reload} />
            ) : undefined
          }
          emptyState={
            <EmptyState
              icon={<TruckIcon className="size-6" />}
              title={filtersActive ? "No transfers match these filters" : "No transfers yet"}
              description={
                filtersActive
                  ? "Try a different status, or clear the search."
                  : "Create one to move stock from one warehouse to another."
              }
            />
          }
        />
        {list.status === "ready" ? (
          <LoadMoreFooter
            loaded={list.rows.length}
            hasMore={list.hasMore}
            loading={list.loadingMore}
            error={list.moreError}
            onLoadMore={list.loadMore}
            noun="transfers"
          />
        ) : null}
      </Card>
    </div>
  );
}
