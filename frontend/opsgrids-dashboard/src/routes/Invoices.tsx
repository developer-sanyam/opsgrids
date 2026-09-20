import { useEffect, useState } from "react";

import { listInvoices } from "@/api";
import type { Invoice } from "@/api/types";
import { formatDate, formatMoney } from "@/lib/money";
import { Card, PageHeader } from "@/ui";

type Load = { state: "loading" } | { state: "error"; message: string } | { state: "ready"; invoices: Invoice[] };

/** Frozen invoices, as the billing service returns them. Nothing here edits money. */
export function InvoicesPage() {
  const [load, setLoad] = useState<Load>({ state: "loading" });

  useEffect(() => {
    let cancelled = false;
    listInvoices()
      .then((invoices) => {
        if (!cancelled) setLoad({ state: "ready", invoices });
      })
      .catch((err: unknown) => {
        if (!cancelled) setLoad({ state: "error", message: err instanceof Error ? err.message : "Something went wrong." });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="flex flex-col gap-6">
      <PageHeader eyebrow="Billing" title="Invoices" description="Every invoice is frozen when it is issued; a correction is a new document, never an edit." />

      {load.state === "loading" ? <p className="text-sm text-muted">Loading…</p> : null}
      {load.state === "error" ? (
        <p role="alert" className="rounded-tile bg-danger-soft px-4 py-3 text-sm font-medium text-danger">
          Couldn't load invoices. {load.message}
        </p>
      ) : null}

      {load.state === "ready" && load.invoices.length === 0 ? (
        <Card className="p-8">
          <p className="text-sm text-muted">No invoices yet. The first one is issued when a trial becomes a paid subscription.</p>
        </Card>
      ) : null}

      {load.state === "ready" && load.invoices.length > 0 ? (
        <Card className="overflow-x-auto p-0">
          <table className="w-full min-w-[36rem] text-left text-sm">
            <thead>
              <tr className="text-xs uppercase tracking-wide text-muted">
                <th className="px-5 py-3 font-semibold">Number</th>
                <th className="px-5 py-3 font-semibold">Period</th>
                <th className="px-5 py-3 font-semibold">State</th>
                <th className="px-5 py-3 text-right font-semibold">Total</th>
              </tr>
            </thead>
            <tbody>
              {load.invoices.map((inv) => (
                <tr key={inv.id} className="border-t border-line">
                  <td className="px-5 py-3 font-semibold text-ink">{inv.number}</td>
                  <td className="px-5 py-3 text-ink-soft">
                    {formatDate(inv.period_start)} – {formatDate(inv.period_end)}
                  </td>
                  <td className="px-5 py-3 text-ink-soft">{inv.state}</td>
                  <td className="px-5 py-3 text-right tabular-nums text-ink">{formatMoney(inv.total_minor, inv.currency)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      ) : null}
    </div>
  );
}
