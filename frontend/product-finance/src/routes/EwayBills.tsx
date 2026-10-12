import { useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { listEwayBills } from "@/api/client";
import type { EwayStatusFilter } from "@/api/client";
import type { EwayBillWithInvoice } from "@/api/types";
import { EWAY_STATUS_FILTERS, ewayStatusTone } from "@/data/compliance";
import { formatDateTime } from "@/lib/format";
import { formatMoney } from "@/lib/money";
import { problemMessage, useAsync } from "@opsgrids/client";
import { Card, DataTable, EmptyState, ErrorState, PageHeader, Pagination, Pill, PortalSimulatedNotice, Skeleton, Tag } from "@/ui";
import type { Column } from "@/ui";
import { TruckIcon } from "@opsgrids/ui";

/**
 * A read-only register: e-Way bills are raised over the internal API by a dispatch system, not
 * from this SPA (`internal.py:152-154`, `catalogue.py:151-153` place that deliberately elsewhere).
 * This screen answers one question — "may that lorry leave" — and offers no write.
 */
export function EwayBillsPage() {
  const [status, setStatus] = useState<EwayStatusFilter>("all");
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(25);

  const bills = useAsync(() => listEwayBills({ status, page, size }), [status, page, size]);
  const rows = useMemo(() => (bills.status === "success" ? bills.data.items : []), [bills]);

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
      render: (row) => (
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
        Read-only: e-Way bills are raised by the dispatch system over its own connection, not from
        this screen.
      </p>
      <PortalSimulatedNotice />

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
        {bills.status === "success" ? (
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
              page={bills.data.page}
              pages={bills.data.pages}
              total={bills.data.total}
              size={bills.data.size}
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
