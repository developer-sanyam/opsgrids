import { useMemo } from "react";
import { Link } from "react-router-dom";

import { listPayments } from "@/api/client";
import { getCustomerById } from "@/api/customers";
import type { Payment } from "@/api/types";
import { customerName } from "@/data/customers";
import { formatDate } from "@/lib/format";
import { formatMoney, sumMinor } from "@/lib/money";
import { useAsync } from "@/lib/useAsync";
import {
  Card,
  DataTable,
  EmptyState,
  ErrorState,
  PageHeader,
  PaymentMethodBadge,
  Skeleton,
} from "@/ui";
import type { Column } from "@/ui";
import { CreditCardIcon } from "@/ui/icons";

/** The invoices a payment was applied to, each a link to that invoice. */
function Allocations({ payment }: { payment: Payment }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {payment.allocations.map((a) => (
        <Link
          key={a.invoice_id}
          to={`/invoices/${a.invoice_id}`}
          onClick={(e) => e.stopPropagation()}
          className="inline-flex items-center gap-1 rounded-full bg-ground px-2.5 py-1 text-xs font-semibold text-ink-soft hover:bg-accent-soft hover:text-accent-ink transition-colors tnum"
        >
          {a.invoice_number}
        </Link>
      ))}
    </div>
  );
}

export function PaymentsPage() {
  const payments = useAsync(listPayments, []);
  // The directory resolves `customer_id` to a name; it never gates the table from rendering.
  const customers = useAsync(getCustomerById, []);
  const customerById = customers.status === "success" ? customers.data : undefined;

  const rows = useMemo(
    () => (payments.status === "success" ? payments.data.items : []),
    [payments],
  );

  const totalReceived = useMemo(
    () => sumMinor(rows.map((p) => p.amount_minor)),
    [rows],
  );

  const columns: Column<Payment>[] = [
    {
      key: "reference",
      header: "Reference",
      render: (p) => (
        <div className="flex flex-col leading-tight">
          <span className="font-semibold text-ink tnum">{p.reference}</span>
          {p.note ? <span className="text-xs text-faint">{p.note}</span> : null}
        </div>
      ),
    },
    {
      key: "customer",
      header: "Customer",
      render: (p) => (
        <span className="text-ink-soft">{customerName(customerById, p.customer_id)}</span>
      ),
    },
    {
      key: "received",
      header: "Received",
      cellClassName: "text-muted tnum whitespace-nowrap hidden sm:table-cell",
      headerClassName: "hidden sm:table-cell",
      render: (p) => formatDate(p.received_date),
    },
    {
      key: "method",
      header: "Method",
      cellClassName: "hidden md:table-cell",
      headerClassName: "hidden md:table-cell",
      render: (p) => <PaymentMethodBadge method={p.method} />,
    },
    {
      key: "allocation",
      header: "Applied to",
      render: (p) => <Allocations payment={p} />,
    },
    {
      key: "amount",
      header: "Amount",
      align: "right",
      cellClassName: "whitespace-nowrap",
      render: (p) => (
        <span className="money font-semibold text-positive">{formatMoney(p.amount_minor)}</span>
      ),
    },
  ];

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Cash received"
        title="Payments"
        actions={
          payments.status === "success" ? (
            <span className="rounded-full bg-surface shadow-card px-4 h-11 inline-flex items-center gap-2 text-sm font-semibold">
              <span className="text-muted font-medium">Received</span>
              <span className="money">{formatMoney(totalReceived)}</span>
            </span>
          ) : undefined
        }
      />

      <p className="-mt-2 max-w-2xl text-sm text-muted">
        Every receipt is allocated against one or more invoices, so a customer's outstanding
        balance always ties back to the cash that settled it.
      </p>

      <Card className="p-2 sm:p-4">
        {payments.status === "loading" ? (
          <div className="flex flex-col gap-2 p-2">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full rounded-tile" />
            ))}
          </div>
        ) : null}
        {payments.status === "error" ? <ErrorState message={payments.error.message} /> : null}
        {payments.status === "success" ? (
          <DataTable
            columns={columns}
            rows={rows}
            rowKey={(p) => p.id}
            caption="Payments received"
            emptyState={
              <EmptyState
                icon={<CreditCardIcon className="size-6" />}
                title="No payments yet"
                description="Received payments will appear here once they're recorded against invoices."
              />
            }
          />
        ) : null}
      </Card>
    </div>
  );
}
