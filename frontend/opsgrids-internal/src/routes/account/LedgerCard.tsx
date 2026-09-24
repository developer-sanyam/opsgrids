import type { CreditLedgerEntry, OrganizationDetail } from "@/api/types";
import { formatDate } from "@/lib/format";
import { Card, CompTag, DataTable, LedgerKindPill, Money, SectionHeader } from "@/ui";
import type { Column } from "@/ui";

/**
 * The account's append-only credit ledger, and the balance now — a SUM over the entries whose
 * window covers today, never a stored column.
 */
export function LedgerCard({ detail }: { detail: OrganizationDetail }) {
  const columns: Column<CreditLedgerEntry>[] = [
    {
      key: "date",
      header: "Date",
      render: (e) => <span className="tnum text-muted">{formatDate(e.created_at)}</span>,
    },
    {
      key: "kind",
      header: "Entry",
      render: (e) => (
        <div className="flex items-center gap-2">
          <LedgerKindPill kind={e.kind} />
          {e.is_comp ? <CompTag /> : null}
        </div>
      ),
    },
    {
      key: "reason",
      header: "Reason",
      cellClassName: "hidden md:table-cell max-w-xs",
      headerClassName: "hidden md:table-cell",
      render: (e) => <span className="text-muted">{e.reason}</span>,
    },
    {
      key: "window",
      header: "Valid until",
      cellClassName: "hidden sm:table-cell",
      headerClassName: "hidden sm:table-cell",
      render: (e) =>
        e.valid_until ? (
          <span className="tnum text-muted">{formatDate(e.valid_until)}</span>
        ) : (
          <span className="text-faint">no expiry</span>
        ),
    },
    {
      key: "amount",
      header: "Amount",
      align: "right",
      render: (e) => <Money minor={e.amount_minor} currency={e.currency} signed className="font-semibold" />,
    },
  ];

  return (
    <Card className="flex flex-col gap-4 p-6">
      <SectionHeader
        title="Credit ledger"
        subtitle="Append-only grants, consumption and reversals"
        action={
          <div className="text-right">
            <span className="block text-xs font-semibold uppercase tracking-wide text-faint">Balance now</span>
            <Money
              minor={detail.comp_balance_minor}
              currency={detail.organization.currency}
              signed
              className="text-lg font-bold"
            />
          </div>
        }
      />
      {detail.ledger.length === 0 ? (
        <p className="py-4 text-sm text-muted">No credit or comp grants on this account.</p>
      ) : (
        <DataTable columns={columns} rows={detail.ledger} rowKey={(e) => e.entry_id} caption="Credit ledger" dense />
      )}
    </Card>
  );
}
