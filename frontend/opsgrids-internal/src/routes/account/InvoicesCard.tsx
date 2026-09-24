import type { Invoice } from "@/api/types";
import { formatDate } from "@/lib/format";
import { Card, DataTable, InvoiceStatePill, Money, SectionHeader } from "@/ui";
import type { Column } from "@/ui";

/** Append-only invoices, frozen at finalize; corrections are new rows. */
export function InvoicesCard({ invoices }: { invoices: Invoice[] }) {
  const columns: Column<Invoice>[] = [
    { key: "number", header: "Invoice", render: (i) => <span className="money text-ink-soft">{i.number}</span> },
    {
      key: "period",
      header: "Period",
      cellClassName: "hidden sm:table-cell",
      headerClassName: "hidden sm:table-cell",
      render: (i) => <span className="tnum text-muted">{formatDate(i.period_start)}</span>,
    },
    { key: "state", header: "State", render: (i) => <InvoiceStatePill state={i.state} /> },
    {
      key: "total",
      header: "Total",
      align: "right",
      render: (i) => <Money minor={i.total_minor} currency={i.currency} precise className="font-semibold" />,
    },
  ];
  return (
    <Card className="flex flex-col gap-4 p-6">
      <SectionHeader title="Invoices" subtitle="Append-only; frozen at finalize" />
      {invoices.length === 0 ? (
        <p className="py-4 text-sm text-muted">No invoices yet — this account has never been billed.</p>
      ) : (
        <DataTable columns={columns} rows={invoices} rowKey={(i) => i.invoice_id} caption="Invoices" dense />
      )}
    </Card>
  );
}
