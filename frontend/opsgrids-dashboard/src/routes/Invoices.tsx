import { useState } from "react";

import { ApiError, problemMessage, useAsync, useCan } from "@opsgrids/client";
import {
  Button,
  Card,
  DataTable,
  Drawer,
  EmptyState,
  ErrorState,
  Skeleton,
  StatusBadge,
  statusTone,
} from "@opsgrids/ui";
import type { Column, StatusTone, TableSelection } from "@opsgrids/ui";

import { getCreditBalance, getOrganization, listInvoices, listSubscriptions } from "@/api";
import type { Invoice, Subscription } from "@/api/types";
import { downloadCsv } from "@/lib/csv";
import { formatDate, formatMoney } from "@/lib/money";
import { PageHeader } from "@/ui";
import { payableInvoiceIds } from "@/ui/billing/payable";
import { PaymentPanel } from "@/ui/billing/PaymentPanel";
import { usePayInvoice } from "@/ui/billing/usePayInvoice";

/** The permission the subscribe action checks (`subscription/api/routes.py`): paying for what you
 *  subscribed to is the same owner decision, and the server enforces it on the checkout call. */
const PAY_PERMISSION = "core.product.activation.write";

/**
 * `void` and `uncollectible` mean something different here than the shared registry's general
 * sense of those words (see `opsgrids-ui/src/lib/status.ts`), so they take an explicit tone
 * rather than the registry's — a `StatusBadge` tone override, listed in the sprint report.
 */
const STATE_TONE_OVERRIDE: Record<string, StatusTone> = {
  void: "danger",
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

/** Frozen invoices, as the billing service returns them. Nothing here edits money — "Pay now"
 *  hands the customer to the payment provider, and only the server's answer says it was paid. */
export function InvoicesPage() {
  const invoices = useAsync(loadInvoices, []);
  const credit = useAsync(loadCreditBalance, []);
  const canPay = useCan(PAY_PERMISSION, "ORG");
  // Which subscriptions are in dunning — the only signal of an unpaid invoice (see `payable.ts`).
  // A failed read simply offers no "Pay now"; the list itself is unaffected.
  const overdue = useAsync(
    (): Promise<Subscription[]> => (canPay ? listSubscriptions() : Promise.resolve([])),
    [canPay],
  );
  const pay = usePayInvoice();
  const [inspecting, setInspecting] = useState<Invoice | null>(null);
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());

  const forbidden = invoices.status === "error" && invoices.error instanceof ApiError && invoices.error.status === 403;

  const payable =
    canPay && invoices.status === "success" && overdue.status === "success"
      ? payableInvoiceIds(invoices.data, overdue.data, pay.paid)
      : new Set<string>();
  const payingId = pay.busy && pay.state.phase !== "idle" ? pay.state.target.invoiceId : null;

  const columns: Column<Invoice>[] =
    invoices.status === "success"
      ? [
          { key: "number", header: "Number", mobile: "title", render: (inv) => <span className="font-semibold text-ink">{inv.number}</span> },
          {
            key: "period",
            header: "Period",
            render: (inv) => (
              <span className="text-ink-soft">
                {formatDate(inv.period_start)} – {formatDate(inv.period_end)}
              </span>
            ),
          },
          {
            key: "state",
            header: "State",
            mobile: "status",
            render: (inv) => (
              <StatusBadge tone={STATE_TONE_OVERRIDE[inv.state] ?? statusTone(inv.state) ?? "neutral"} label={inv.state} />
            ),
          },
          {
            key: "total",
            header: "Total",
            align: "right",
            cellClassName: "tnum",
            render: (inv) => <span className="text-ink">{formatMoney(inv.total_minor, inv.currency)}</span>,
          },
          {
            key: "pay",
            header: "",
            align: "right",
            mobile: "actions",
            render: (inv) =>
              payable.has(inv.id) ? (
                <Button
                  size="sm"
                  loading={payingId === inv.id}
                  disabled={pay.busy}
                  // The row opens the invoice's drawer on click and on Enter/Space; paying is not that.
                  onKeyDown={(event) => event.stopPropagation()}
                  onClick={(event) => {
                    event.stopPropagation();
                    void pay.start({ invoiceId: inv.id, label: `Invoice ${inv.number}` });
                  }}
                >
                  Pay now<span className="sr-only"> invoice {inv.number}</span>
                </Button>
              ) : null,
          },
        ]
      : [];

  const invoiceSelection: TableSelection<Invoice> | undefined =
    invoices.status === "success"
      ? {
          selected,
          onToggle: (key, next) =>
            setSelected((prev) => {
              const nextSet = new Set(prev);
              if (next) nextSet.add(key);
              else nextSet.delete(key);
              return nextSet;
            }),
          onToggleAll: (next) => setSelected(next ? new Set(invoices.data.map((inv) => inv.id)) : new Set()),
          label: (inv) => inv.number,
        }
      : undefined;

  function exportSelectedInvoices() {
    if (invoices.status !== "success") return;
    const chosen = invoices.data.filter((inv) => selected.has(inv.id));
    downloadCsv(
      "invoices.csv",
      ["Number", "Period start", "Period end", "State", "Total"],
      chosen.map((inv) => [
        inv.number,
        inv.period_start,
        inv.period_end,
        inv.state,
        formatMoney(inv.total_minor, inv.currency),
      ]),
    );
  }

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

      <PaymentPanel pay={pay} />

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

      {invoices.status === "success" ? (
        <Card className="p-0">
          <DataTable
            rows={invoices.data}
            rowKey={(inv) => inv.id}
            columns={columns}
            mobileCard="auto"
            selection={invoiceSelection}
            bulkActions={
              <Button variant="outline" size="sm" onClick={exportSelectedInvoices}>
                Export selected
              </Button>
            }
            onRowClick={(inv) => setInspecting(inv)}
            emptyState={
              <EmptyState
                title="No invoices yet"
                description="The first one is issued when a trial becomes a paid subscription."
              />
            }
          />
        </Card>
      ) : null}

      <Drawer
        open={inspecting !== null}
        onClose={() => setInspecting(null)}
        title={inspecting ? `Invoice ${inspecting.number}` : "Invoice"}
        description={
          inspecting ? `${formatDate(inspecting.period_start)} – ${formatDate(inspecting.period_end)}` : undefined
        }
      >
        {inspecting ? (
          inspecting.lines.length === 0 ? (
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
                {inspecting.lines.map((line) => (
                  <tr key={line.id} className="border-t border-line">
                    <td className="py-1.5 pr-3 text-ink-soft">{line.description}</td>
                    <td className="py-1.5 pr-3 tabular-nums text-ink-soft">{line.quantity}</td>
                    <td className="py-1.5 text-right tabular-nums text-ink">{formatMoney(line.amount_minor, inspecting.currency)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )
        ) : null}
      </Drawer>
    </div>
  );
}
