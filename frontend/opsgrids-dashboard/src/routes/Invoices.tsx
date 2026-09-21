import { Fragment, useState } from "react";

import { ApiError, problemMessage, useAsync } from "@opsgrids/client";
import { Card, ChevronRight, EmptyState, ErrorState, Skeleton, Tag } from "@opsgrids/ui";
import type { TagTone } from "@opsgrids/ui";

import { getCreditBalance, getOrganization, listInvoices } from "@/api";
import type { Invoice } from "@/api/types";
import { formatDate, formatMoney } from "@/lib/money";
import { PageHeader } from "@/ui";

const STATE_TONE: Record<string, TagTone> = {
  draft: "neutral",
  open: "info",
  paid: "positive",
  void: "negative",
  uncollectible: "warning",
};

async function loadInvoices(): Promise<Invoice[]> {
  return listInvoices();
}

/** The organisation's own currency, read once so the credit balance is asked for in the right one. */
async function loadCreditBalance(): Promise<{ balance_minor: number; currency: string } | null> {
  const org = await getOrganization();
  return getCreditBalance(org.currency);
}

/** Frozen invoices, as the billing service returns them. Nothing here edits money. */
export function InvoicesPage() {
  const invoices = useAsync(loadInvoices, []);
  const credit = useAsync(loadCreditBalance, []);
  const [expanded, setExpanded] = useState<string | null>(null);

  const forbidden = invoices.status === "error" && invoices.error instanceof ApiError && invoices.error.status === 403;

  return (
    <div className="flex flex-col gap-6">
      <PageHeader eyebrow="Billing" title="Invoices" description="Every invoice is frozen when it is issued; a correction is a new document, never an edit." />

      {credit.status === "success" && credit.data ? (
        <Card className="flex items-center justify-between gap-4 p-5">
          <div>
            <p className="text-xs font-semibold uppercase tracking-wide text-faint">Credit balance</p>
            <p className="tnum text-2xl font-extrabold text-ink">
              {formatMoney(credit.data.balance_minor, credit.data.currency)}
            </p>
          </div>
        </Card>
      ) : null}

      {invoices.status === "loading" ? <Skeleton className="h-64 w-full rounded-card" /> : null}

      {invoices.status === "error" && forbidden ? (
        <ErrorState
          title="You don't have access to billing"
          message="Ask your organisation's owner for the billing permission."
          tone="warning"
        />
      ) : null}
      {invoices.status === "error" && !forbidden ? (
        <ErrorState title="Couldn't load invoices" message={problemMessage(invoices.error)} onRetry={invoices.reload} />
      ) : null}

      {invoices.status === "success" && invoices.data.length === 0 ? (
        <EmptyState
          title="No invoices yet"
          description="The first one is issued when a trial becomes a paid subscription."
        />
      ) : null}

      {invoices.status === "success" && invoices.data.length > 0 ? (
        <Card className="overflow-x-auto p-0">
          <table className="w-full min-w-[36rem] text-left text-sm">
            <thead>
              <tr className="text-xs uppercase tracking-wide text-muted">
                <th className="px-5 py-3 font-semibold">
                  <span className="sr-only">Expand</span>
                </th>
                <th className="px-5 py-3 font-semibold">Number</th>
                <th className="px-5 py-3 font-semibold">Period</th>
                <th className="px-5 py-3 font-semibold">State</th>
                <th className="px-5 py-3 text-right font-semibold">Total</th>
              </tr>
            </thead>
            <tbody>
              {invoices.data.map((inv) => {
                const open = expanded === inv.id;
                return (
                  <Fragment key={inv.id}>
                    <tr className="border-t border-line">
                      <td className="px-3 py-3">
                        <button
                          type="button"
                          onClick={() => setExpanded(open ? null : inv.id)}
                          aria-expanded={open}
                          aria-controls={`invoice-${inv.id}-lines`}
                          aria-label={`${open ? "Collapse" : "Expand"} invoice ${inv.number}`}
                          className="grid size-8 place-items-center rounded-full text-ink-soft transition-colors hover:bg-ground"
                        >
                          <ChevronRight className={open ? "size-4 rotate-90 transition-transform" : "size-4 transition-transform"} />
                        </button>
                      </td>
                      <td className="px-5 py-3 font-semibold text-ink">{inv.number}</td>
                      <td className="px-5 py-3 text-ink-soft">
                        {formatDate(inv.period_start)} – {formatDate(inv.period_end)}
                      </td>
                      <td className="px-5 py-3">
                        <Tag tone={STATE_TONE[inv.state] ?? "neutral"}>{inv.state}</Tag>
                      </td>
                      <td className="px-5 py-3 text-right tabular-nums text-ink">{formatMoney(inv.total_minor, inv.currency)}</td>
                    </tr>
                    {open ? (
                      <tr id={`invoice-${inv.id}-lines`} className="border-t border-line bg-ground/50">
                        <td colSpan={5} className="px-5 py-4">
                          {inv.lines.length === 0 ? (
                            <p className="text-sm text-muted">This invoice has no line items.</p>
                          ) : (
                            <table className="w-full text-left text-sm">
                              <thead>
                                <tr className="text-xs uppercase tracking-wide text-faint">
                                  <th className="py-1.5 pr-3 font-semibold">Description</th>
                                  <th className="py-1.5 pr-3 font-semibold">Quantity</th>
                                  <th className="py-1.5 text-right font-semibold">Amount</th>
                                </tr>
                              </thead>
                              <tbody>
                                {inv.lines.map((line) => (
                                  <tr key={line.id}>
                                    <td className="py-1.5 pr-3 text-ink-soft">{line.description}</td>
                                    <td className="py-1.5 pr-3 tabular-nums text-ink-soft">{line.quantity}</td>
                                    <td className="py-1.5 text-right tabular-nums text-ink">{formatMoney(line.amount_minor, inv.currency)}</td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          )}
                        </td>
                      </tr>
                    ) : null}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </Card>
      ) : null}
    </div>
  );
}
