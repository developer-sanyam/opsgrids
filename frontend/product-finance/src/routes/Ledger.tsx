import { useMemo, useState } from "react";

import { getLedger } from "@/api/client";
import type { LedgerAccount, LedgerReport } from "@/api/types";
import { formatDate } from "@/lib/format";
import { formatAmount, formatMoney } from "@/lib/money";
import { useAsync } from "@/lib/useAsync";
import {
  Button,
  Card,
  ErrorState,
  PageHeader,
  SectionHeader,
  Skeleton,
} from "@/ui";
import { DownloadIcon } from "@/ui/icons";

/** Revenue less contra-revenue; expenses; and the profit that falls out of the two. */
function summarise(accounts: LedgerAccount[]) {
  const grossRevenue = accounts
    .filter((a) => a.type === "revenue")
    .reduce((sum, a) => sum + a.credit_minor, 0);
  const contra = accounts
    .filter((a) => a.type === "contra_revenue")
    .reduce((sum, a) => sum + a.debit_minor, 0);
  const netRevenue = grossRevenue - contra;
  const expenses = accounts
    .filter((a) => a.type === "expense")
    .reduce((sum, a) => sum + a.debit_minor, 0);
  const netProfit = netRevenue - expenses;
  const margin = netRevenue === 0 ? 0 : Math.round((netProfit / netRevenue) * 1000) / 10;
  return { netRevenue, expenses, netProfit, margin };
}

/** Build a CSV of the trial-balance rows and hand it to the browser as a download. */
function exportCsv(report: LedgerReport): void {
  const header = ["Code", "Account", "Type", "Debit (INR)", "Credit (INR)"];
  const lines = report.accounts.map((a) =>
    [
      a.code,
      `"${a.name}"`,
      a.type,
      (a.debit_minor / 100).toFixed(2),
      (a.credit_minor / 100).toFixed(2),
    ].join(","),
  );
  const csv = [header.join(","), ...lines].join("\n");
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `atmora-finance-trial-balance-${report.as_of}.csv`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

const TYPE_LABEL: Record<LedgerAccount["type"], string> = {
  revenue: "Revenue",
  contra_revenue: "Contra revenue",
  expense: "Expense",
};

function SummaryTile({
  label,
  value,
  tone,
  caption,
}: {
  label: string;
  value: string;
  tone: "neutral" | "positive";
  caption: string;
}) {
  return (
    <div className="flex flex-col gap-1 rounded-tile bg-ground px-5 py-4">
      <span className="text-sm font-semibold text-muted">{label}</span>
      <span
        className={`money display text-2xl font-extrabold ${tone === "positive" ? "text-positive" : "text-ink"}`}
      >
        {value}
      </span>
      <span className="text-xs text-faint">{caption}</span>
    </div>
  );
}

export function LedgerPage() {
  const ledger = useAsync(getLedger, []);
  const [exported, setExported] = useState(false);

  const summary = useMemo(
    () => (ledger.status === "success" ? summarise(ledger.data.accounts) : null),
    [ledger],
  );

  const totals = useMemo(() => {
    if (ledger.status !== "success") return { debit: 0, credit: 0 };
    return ledger.data.accounts.reduce(
      (acc, a) => ({ debit: acc.debit + a.debit_minor, credit: acc.credit + a.credit_minor }),
      { debit: 0, credit: 0 },
    );
  }, [ledger]);

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Books"
        title="Ledger & Reports"
        actions={
          <Button
            variant="outline"
            size="md"
            leadingIcon={<DownloadIcon className="size-4" />}
            disabled={ledger.status !== "success"}
            onClick={() => {
              if (ledger.status === "success") {
                exportCsv(ledger.data);
                setExported(true);
                window.setTimeout(() => setExported(false), 4000);
              }
            }}
          >
            Export CSV
          </Button>
        }
      />

      <p className="-mt-2 max-w-2xl text-sm text-muted" aria-live="polite">
        {exported
          ? "Trial balance exported as CSV."
          : ledger.status === "success"
            ? `Profit & loss and trial balance for ${ledger.data.period_label}, as of ${formatDate(ledger.data.as_of)}.`
            : "Profit & loss and trial balance, straight from the ledger."}
      </p>

      {ledger.status === "loading" ? (
        <Card className="p-6">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            {Array.from({ length: 3 }).map((_, i) => (
              <Skeleton key={i} className="h-24 w-full rounded-tile" />
            ))}
          </div>
          <div className="mt-6 flex flex-col gap-2">
            {Array.from({ length: 8 }).map((_, i) => (
              <Skeleton key={i} className="h-10 w-full rounded-tile" />
            ))}
          </div>
        </Card>
      ) : null}

      {ledger.status === "error" ? (
        <Card className="p-6">
          <ErrorState message={ledger.error.message} />
        </Card>
      ) : null}

      {ledger.status === "success" && summary ? (
        <>
          {/* P&L summary tiles */}
          <section aria-label="Profit and loss summary" className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            <SummaryTile
              label="Net revenue"
              value={formatMoney(summary.netRevenue)}
              tone="neutral"
              caption="After returns & credit notes"
            />
            <SummaryTile
              label="Total expenses"
              value={formatMoney(summary.expenses)}
              tone="neutral"
              caption="Cost of sales + operating"
            />
            <SummaryTile
              label="Net profit"
              value={formatMoney(summary.netProfit)}
              tone="positive"
              caption={`${summary.margin}% net margin`}
            />
          </section>

          {/* Trial balance */}
          <Card className="flex flex-col gap-4 p-6">
            <SectionHeader title="Trial balance" subtitle="Every account, debit and credit" />
            <div className="overflow-x-auto">
              <table className="w-full border-collapse text-sm">
                <caption className="sr-only">Trial balance by account</caption>
                <thead>
                  <tr className="border-b border-line text-xs font-semibold uppercase tracking-wide text-faint">
                    <th scope="col" className="py-2.5 pr-3 text-left">Code</th>
                    <th scope="col" className="py-2.5 px-3 text-left">Account</th>
                    <th scope="col" className="py-2.5 px-3 text-left hidden sm:table-cell">Type</th>
                    <th scope="col" className="py-2.5 px-3 text-right">Debit</th>
                    <th scope="col" className="py-2.5 pl-3 text-right">Credit</th>
                  </tr>
                </thead>
                <tbody>
                  {ledger.data.accounts.map((a) => (
                    <tr key={a.code} className="border-b border-line/70 hover:bg-ground/60 transition-colors">
                      <td className="py-3 pr-3 align-middle font-semibold text-ink tnum">{a.code}</td>
                      <td className="py-3 px-3 align-middle text-ink-soft">{a.name}</td>
                      <td className="py-3 px-3 align-middle text-muted hidden sm:table-cell">
                        {TYPE_LABEL[a.type]}
                      </td>
                      <td className="py-3 px-3 text-right align-middle money text-ink-soft">
                        {a.debit_minor > 0 ? `₹${formatAmount(a.debit_minor)}` : <span className="text-faint">—</span>}
                      </td>
                      <td className="py-3 pl-3 text-right align-middle money text-ink-soft">
                        {a.credit_minor > 0 ? `₹${formatAmount(a.credit_minor)}` : <span className="text-faint">—</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
                <tfoot>
                  <tr className="border-t-2 border-line font-bold text-ink">
                    <td className="py-3 pr-3" colSpan={3}>
                      Totals
                    </td>
                    <td className="py-3 px-3 text-right money">₹{formatAmount(totals.debit)}</td>
                    <td className="py-3 pl-3 text-right money">₹{formatAmount(totals.credit)}</td>
                  </tr>
                </tfoot>
              </table>
            </div>
          </Card>
        </>
      ) : null}
    </div>
  );
}
