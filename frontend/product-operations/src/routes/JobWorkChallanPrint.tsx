import { Link, useParams } from "react-router-dom";

import { getChallan } from "@/api/client";
import { problemMessage, useAsync, useAuth } from "@opsgrids/client";
import { formatDateTime, formatMoneyPrecise } from "@/lib/format";
import { formatQty } from "@/lib/quantity";
import { Button, ErrorState, Skeleton } from "@/ui";
import { ArrowRightIcon } from "@opsgrids/ui";

/**
 * The delivery challan for goods sent for job work (CGST Rule 55), laid out to print. Entirely
 * client-rendered from the frozen challan the server holds (`GET /operations/jobwork/challans/{id}`
 * — immutable once issued, so a reprint is always the same document) and printed with
 * `window.print()`.
 *
 * Deliberately its own route with no `AppShell` chrome (see `main.tsx`), the same reasoning the
 * location label sheet gives: a sidebar has no place on a printed page, and this sidesteps
 * fighting it with print stylesheets. The Back link and the print button are hidden at print time
 * (`print:hidden`); everything else is the document. Quantities are the decimal strings the server
 * sent; money is minor units formatted once, here.
 */
export function JobWorkChallanPrintPage() {
  const { id = "" } = useParams();
  const { user } = useAuth();
  const state = useAsync(() => getChallan(id), [id]);

  const organizationName =
    user?.organizations.find((o) => o.organization_id === user.organization_id)?.name ?? "";

  return (
    <div className="min-h-dvh bg-ground">
      <div className="print:hidden flex flex-wrap items-center justify-between gap-4 border-b border-line bg-surface px-6 py-4">
        <Link
          to={state.status === "success" ? `/jobwork/orders/${state.data.order_id}` : "/jobwork/orders"}
          className="inline-flex items-center gap-2 text-sm font-semibold text-muted hover:text-ink transition-colors"
        >
          <ArrowRightIcon className="size-4 rotate-180" />
          Back to the order
        </Link>
        <Button onClick={() => window.print()} disabled={state.status !== "success"}>
          Print
        </Button>
      </div>

      <div className="mx-auto flex max-w-3xl flex-col gap-6 p-6 print:max-w-none print:p-0">
        {state.status === "loading" ? (
          <div className="flex flex-col gap-4 print:hidden" aria-busy="true" aria-label="Loading challan">
            <Skeleton className="h-16 w-full rounded-tile" />
            <Skeleton className="h-48 w-full rounded-tile" />
          </div>
        ) : null}
        {state.status === "error" ? (
          <ErrorState
            title="Couldn't load this challan"
            message={problemMessage(state.error)}
            onRetry={state.reload}
          />
        ) : null}
        {state.status === "success" ? (
          <article
            aria-label={`Delivery challan ${state.data.challan_number}`}
            className="flex flex-col gap-6 rounded-tile border border-line bg-surface p-6 print:border-0 print:p-0"
          >
            <header className="flex flex-wrap items-start justify-between gap-4 border-b border-line pb-4">
              <div>
                <h1 className="text-2xl font-semibold text-ink">Delivery challan</h1>
                <p className="text-sm text-muted">Goods sent for job work</p>
              </div>
              <dl className="text-right text-sm">
                <dt className="text-muted">Challan no.</dt>
                <dd className="tnum text-lg font-semibold text-ink">{state.data.challan_number}</dd>
                <dt className="mt-2 text-muted">Date</dt>
                <dd className="tnum text-ink">{formatDateTime(state.data.issued_at)}</dd>
              </dl>
            </header>

            <section className="grid grid-cols-1 gap-6 sm:grid-cols-2" aria-label="Parties">
              <div>
                <h2 className="text-xs font-semibold uppercase tracking-wide text-muted">From</h2>
                <p className="text-ink">{organizationName || "—"}</p>
                <p className="text-sm text-muted">Order {state.data.order_number}</p>
              </div>
              <div>
                <h2 className="text-xs font-semibold uppercase tracking-wide text-muted">To (job worker)</h2>
                <p className="text-ink">{state.data.supplier_name || "—"}</p>
                {state.data.process ? <p className="text-sm text-muted">Process: {state.data.process}</p> : null}
              </div>
            </section>

            <table className="w-full border-collapse text-sm">
              <caption className="sr-only">Goods on this challan</caption>
              <thead>
                <tr className="border-b border-line text-left text-muted">
                  <th scope="col" className="py-2 pr-3 font-medium">
                    Item
                  </th>
                  <th scope="col" className="py-2 pr-3 text-right font-medium">
                    Quantity
                  </th>
                  <th scope="col" className="py-2 pr-3 text-right font-medium">
                    Rate
                  </th>
                  <th scope="col" className="py-2 text-right font-medium">
                    Value
                  </th>
                </tr>
              </thead>
              <tbody>
                {state.data.lines.map((line, index) => (
                  <tr key={`${line.sku}-${index}`} className="border-b border-line">
                    <td className="py-2 pr-3 text-ink">{line.sku}</td>
                    <td className="tnum py-2 pr-3 text-right">
                      {formatQty(line.quantity)} {line.uom}
                    </td>
                    <td className="tnum py-2 pr-3 text-right">
                      {formatMoneyPrecise(line.unit_value_minor, state.data.currency)}
                    </td>
                    <td className="tnum py-2 text-right">
                      {formatMoneyPrecise(line.value_minor, state.data.currency)}
                    </td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr>
                  <th scope="row" colSpan={3} className="py-2 pr-3 text-right font-semibold text-ink">
                    Total value
                  </th>
                  <td className="tnum py-2 text-right text-base font-semibold text-ink">
                    {formatMoneyPrecise(state.data.total_value_minor, state.data.currency)}
                  </td>
                </tr>
              </tfoot>
            </table>

            <section className="grid grid-cols-1 gap-4 text-sm sm:grid-cols-3" aria-label="Transport">
              <div>
                <h2 className="text-xs font-semibold uppercase tracking-wide text-muted">Vehicle</h2>
                <p className="tnum text-ink">{state.data.vehicle_no || "—"}</p>
              </div>
              <div>
                <h2 className="text-xs font-semibold uppercase tracking-wide text-muted">Transporter ID</h2>
                <p className="tnum text-ink">{state.data.transporter_id || "—"}</p>
              </div>
              <div>
                <h2 className="text-xs font-semibold uppercase tracking-wide text-muted">E-way bill</h2>
                <p className="tnum text-ink">
                  {state.data.eway_bill_no
                    ? state.data.eway_bill_no
                    : state.data.eway_status === "NOT_REQUIRED"
                      ? "Not required"
                      : "Pending"}
                </p>
              </div>
            </section>

            <footer className="flex items-end justify-between gap-6 border-t border-line pt-8 text-sm text-muted">
              <span>Goods are sent for job work and remain the property of the sender.</span>
              <span className="min-w-40 border-t border-ink pt-1 text-center">Authorised signatory</span>
            </footer>
          </article>
        ) : null}
      </div>
    </div>
  );
}
