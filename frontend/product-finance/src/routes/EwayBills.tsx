import { useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { listEwayBills } from "@/api/client";
import type { EwayStatusFilter } from "@/api/client";
import type { EwayBillWithInvoice, Page } from "@/api/types";
import { EWAY_STATUS_FILTERS, ewayStateView, ewayStatusTone } from "@/data/compliance";
import { formatDateTime } from "@/lib/format";
import { formatMoney } from "@/lib/money";
import { useQuietRefresh } from "@/lib/useQuietRefresh";
import { problemMessage, useAsync } from "@opsgrids/client";
import {
  Card,
  DataTable,
  EmptyState,
  ErrorState,
  EwayStateCell,
  PageHeader,
  Pagination,
  Pill,
  Skeleton,
  Tag,
} from "@/ui";
import type { Column } from "@/ui";
import { TruckIcon } from "@opsgrids/ui";

/** How often the register re-reads while something is being retried, so a retry that succeeded
 *  or gave up shows up without a reload. Not while everything is at rest. */
const ACTIVE_REFRESH_MS = 15_000;

/**
 * The e-Way register. Bills are raised over the internal API by a dispatch system, not from this
 * SPA (`internal.py`, `catalogue.py` place that deliberately elsewhere), so the screen answers
 * one question — "may that lorry leave" — and offers one write: **retry now**, on a generation the
 * platform is already retrying or gave up on for a reason that is not the portal's verdict on the
 * consignment.
 *
 * The register lists every attempt, so only a document's *current* row carries the retry story
 * (attempt, countdown, the portal's reason, the remedy); an older `pending` row is history.
 */
export function EwayBillsPage() {
  const [status, setStatus] = useState<EwayStatusFilter>("all");
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(25);

  const bills = useAsync(() => listEwayBills({ status, page, size }), [status, page, size]);
  const loaded = bills.status === "success" ? bills.data : null;

  // A background read that replaces the loaded page without going back through `loading`: a table
  // that flashed to skeletons every fifteen seconds would unmount the countdowns and any dialog
  // open on a row.
  const { shown, refresh } = useQuietRefresh<Page<EwayBillWithInvoice>>(
    loaded,
    () => listEwayBills({ status, page, size }),
    {
      active: (current) =>
        current.items.some(
          (row) => row.is_current === true && row.state != null && ewayStateView(row.state)?.active === true,
        ),
      intervalMs: ACTIVE_REFRESH_MS,
    },
  );
  const rows = useMemo(() => shown?.items ?? [], [shown]);

  const columns: Column<EwayBillWithInvoice>[] = [
    {
      key: "invoice",
      header: "Invoice",
      render: (row) => (
        <Link
          to={`/invoices/${row.bill.invoice_id}`}
          className="font-semibold text-ink hover:text-accent transition-colors tnum"
        >
          {row.invoice_number}
        </Link>
      ),
    },
    {
      key: "customer",
      header: "Customer",
      render: (row) => <span className="text-ink-soft">{row.customer_name}</span>,
    },
    {
      key: "bill_no",
      header: "e-Way bill no.",
      cellClassName: "hidden sm:table-cell",
      headerClassName: "hidden sm:table-cell",
      render: (row) => <span className="money text-sm text-ink-soft">{row.bill.eway_bill_no ?? "—"}</span>,
    },
    {
      key: "transport",
      header: "Transport",
      cellClassName: "hidden md:table-cell",
      headerClassName: "hidden md:table-cell",
      render: (row) => (
        <div className="flex flex-col leading-tight">
          <span className="text-sm text-ink-soft capitalize">{row.bill.transport_mode}</span>
          <span className="text-xs text-muted">{row.bill.distance_km} km</span>
        </div>
      ),
    },
    {
      key: "valid",
      header: "Valid until",
      cellClassName: "whitespace-nowrap hidden lg:table-cell",
      headerClassName: "hidden lg:table-cell",
      render: (row) => formatDateTime(row.bill.valid_until),
    },
    {
      key: "value",
      header: "Consignment",
      align: "right",
      cellClassName: "whitespace-nowrap",
      render: (row) => <span className="money font-semibold text-ink">{formatMoney(row.bill.consignment_value_minor)}</span>,
    },
    {
      key: "status",
      header: "Status",
      align: "right",
      render: (row) =>
        row.is_current === true && row.state != null && ewayStateView(row.state) !== null ? (
          <EwayStateCell
            invoiceId={row.bill.invoice_id}
            invoiceNumber={row.invoice_number}
            state={row.state}
            onRetried={refresh}
          />
        ) : (
          <Tag className={ewayStatusTone(row.bill.effective_status)}>
            {row.bill.effective_status}
          </Tag>
        ),
    },
  ];

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader eyebrow="GST compliance" title="e-Way bills" />
      <p className="-mt-2 max-w-2xl text-sm text-muted">
        e-Way bills are raised by the dispatch system over its own connection, not from this
        screen. When the portal does not answer, the same consignment is sent again automatically;
        here you can see how far that has got and, where it would help, retry it now.
      </p>

      <Card className="flex flex-col gap-3 p-4 sm:p-5">
        <div className="flex items-center gap-1.5 overflow-x-auto -mx-1 px-1">
          {EWAY_STATUS_FILTERS.map((f) => (
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
        </div>
      </Card>

      <Card className="p-2 sm:p-4">
        {bills.status === "loading" ? (
          <div className="flex flex-col gap-2 p-2">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full rounded-tile" />
            ))}
          </div>
        ) : null}
        {bills.status === "error" ? (
          <ErrorState
            title="Couldn't load e-Way bills"
            message={problemMessage(bills.error)}
            onRetry={bills.reload}
          />
        ) : null}
        {shown !== null ? (
          <>
            <DataTable
              columns={columns}
              rows={rows}
              rowKey={(row) => row.bill.id}
              caption="e-Way bills and their validity"
              emptyState={
                <EmptyState
                  icon={<TruckIcon className="size-6" />}
                  title="No e-Way bills match"
                  description="Nothing matches this filter, or none have been raised yet."
                />
              }
            />
            <Pagination
              page={shown.page}
              pages={shown.pages}
              total={shown.total}
              size={shown.size}
              noun="e-Way bills"
              onPageChange={setPage}
              onSizeChange={(n) => {
                setSize(n);
                setPage(1);
              }}
            />
          </>
        ) : null}
      </Card>
    </div>
  );
}
