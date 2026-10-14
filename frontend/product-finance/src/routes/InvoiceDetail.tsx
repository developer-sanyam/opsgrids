import { useCallback, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { getInvoice } from "@/api/client";
import { getCustomerById } from "@/api/customers";
import type { Invoice } from "@/api/types";
import { customerName, getCustomer } from "@/data/customers";
import { outstandingMinor } from "@/data/derive";
import { formatDate } from "@/lib/format";
import { formatAmount, formatMoney } from "@/lib/money";
import { Can, problemMessage, useAsync } from "@opsgrids/client";
import {
  Avatar,
  Button,
  Card,
  CompliancePanel,
  ErrorState,
  InvoiceStatusPill,
  Skeleton,
  Tag,
} from "@/ui";
import { ArrowLeftIcon, CheckCircleIcon, PlusIcon, useToast } from "@opsgrids/ui";
import { PaymentFormDialog } from "@/forms/PaymentFormDialog";

/** A labelled figure in the totals footer. */
function TotalRow({
  label,
  value,
  strong = false,
  tone = "default",
}: {
  label: string;
  value: string;
  strong?: boolean;
  tone?: "default" | "muted" | "positive";
}) {
  const valueTone =
    tone === "muted" ? "text-muted" : tone === "positive" ? "text-positive" : "text-ink";
  return (
    <div className="flex items-baseline justify-between gap-6">
      <span className={strong ? "text-sm font-bold text-ink" : "text-sm text-muted"}>{label}</span>
      <span
        className={`money ${strong ? "text-lg font-extrabold" : "text-sm font-semibold"} ${valueTone}`}
      >
        {value}
      </span>
    </div>
  );
}

export function InvoiceDetailPage() {
  const { id = "" } = useParams();
  const loader = useCallback(() => getInvoice(id), [id]);
  const invoice = useAsync(loader, [id]);

  return (
    <div className="rise flex flex-col gap-6">
      <Link
        to="/invoices"
        className="relative hit-44 inline-flex items-center gap-1.5 text-sm font-semibold text-muted hover:text-ink transition-colors self-start"
      >
        <ArrowLeftIcon className="size-4" />
        Back to invoices
      </Link>

      {invoice.status === "loading" ? (
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
          <Card className="p-6 lg:col-span-2">
            <Skeleton className="h-8 w-56" />
            <div className="mt-6 flex flex-col gap-3">
              {Array.from({ length: 5 }).map((_, i) => (
                <Skeleton key={i} className="h-10 w-full rounded-tile" />
              ))}
            </div>
          </Card>
          <Card className="p-6">
            <Skeleton className="h-40 w-full rounded-tile" />
          </Card>
        </div>
      ) : null}

      {invoice.status === "error" ? (
        <Card className="p-6">
          <ErrorState message={problemMessage(invoice.error)} onRetry={invoice.reload} />
        </Card>
      ) : null}

      {invoice.status === "success" ? (
        <InvoiceBody invoice={invoice.data} onReload={invoice.reload} />
      ) : null}
    </div>
  );
}

function InvoiceBody({ invoice, onReload }: { invoice: Invoice; onReload: () => void }) {
  const navigate = useNavigate();
  const toast = useToast();
  // The directory resolves `customer_id` to a display record; it never gates the document.
  const customers = useAsync(getCustomerById, []);
  const customerById = customers.status === "success" ? customers.data : undefined;
  const customer = getCustomer(customerById, invoice.customer_id);
  const billedTo = customerName(customerById, invoice.customer_id);
  const isCreditNote = invoice.document_type === "credit_note";
  const outstanding = outstandingMinor(invoice);
  const canRecord =
    !isCreditNote && (invoice.status === "sent" || invoice.status === "overdue") && outstanding > 0;
  const canCorrect =
    !isCreditNote && (invoice.status === "sent" || invoice.status === "overdue" || invoice.status === "paid");
  const sign = (v: number) => (v < 0 ? "−" : "");
  const [recording, setRecording] = useState(false);

  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-3 items-start">
      {/* Left — the document */}
      <Card className="flex flex-col gap-6 p-6 lg:col-span-2 lg:p-8">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="flex flex-col gap-2">
            <div className="flex items-center gap-2.5">
              <h1 className="display text-2xl font-extrabold tnum">{invoice.number}</h1>
              <InvoiceStatusPill status={invoice.status} />
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <Tag className={isCreditNote ? "bg-accent-soft text-accent-ink" : ""}>
                {isCreditNote ? "Credit note" : "Tax invoice"}
              </Tag>
              <Tag>GST {invoice.tax_rate_pct}%</Tag>
              <Tag>{invoice.currency}</Tag>
            </div>
          </div>
          <div className="flex flex-wrap items-start gap-3">
            <div className="flex flex-col items-end gap-1 text-sm">
              <span className="text-muted">
                Issued <span className="font-semibold text-ink-soft tnum">{formatDate(invoice.issued_date)}</span>
              </span>
              <span className="text-muted">
                Due <span className="font-semibold text-ink-soft tnum">{formatDate(invoice.due_date)}</span>
              </span>
            </div>
            <Button
              variant="outline"
              size="sm"
              onClick={() => navigate(`/invoices/${encodeURIComponent(invoice.id)}/print`)}
            >
              Download or print
            </Button>
            {canCorrect ? (
              <Can permission="finance.billing.invoice.issue" scope="ORG">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => navigate(`/invoices/new?corrects=${invoice.id}`)}
                >
                  Issue credit note
                </Button>
              </Can>
            ) : null}
          </div>
        </div>

        <p className="rounded-tile bg-ground px-4 py-3 text-sm text-muted">
          An issued invoice cannot be edited or deleted. A mistake here is corrected by{" "}
          <button
            type="button"
            onClick={() => canCorrect && navigate(`/invoices/new?corrects=${invoice.id}`)}
            disabled={!canCorrect}
            className="relative hit-44 font-semibold text-ink-soft underline decoration-dotted underline-offset-2 disabled:cursor-not-allowed disabled:no-underline"
          >
            issuing a credit note
          </button>{" "}
          against it, never by editing this document.
        </p>

        {invoice.corrects_invoice_id ? (
          <Link
            to={`/invoices/${invoice.corrects_invoice_id}`}
            className="inline-flex w-fit items-center gap-1.5 rounded-tile bg-accent-soft px-3 py-2 text-xs font-semibold text-accent-ink hover:bg-accent/30 transition-colors"
          >
            View the invoice this corrects
          </Link>
        ) : null}

        {/* Bill-to */}
        <div className="flex items-center gap-3 border-y border-line py-4">
          <Avatar name={billedTo} size="lg" />
          <div className="flex min-w-0 flex-col leading-tight">
            <span className="text-xs font-semibold uppercase tracking-wide text-faint">Billed to</span>
            <span className="truncate text-base font-bold text-ink">{billedTo}</span>
            {customer ? (
              <span className="truncate text-sm text-muted">
                {customer.contact_name} · {customer.email}
              </span>
            ) : null}
            {customer ? (
              <span className="truncate text-xs text-faint tnum">
                GSTIN {customer.gstin ?? "—"} · {customer.city}, {customer.state}
              </span>
            ) : null}
          </div>
        </div>

        {/* Line items */}
        <div className="overflow-x-auto">
          <table className="stack-rows w-full border-collapse text-sm">
            <caption className="sr-only">Line items for {invoice.number}</caption>
            <thead>
              <tr className="border-b border-line text-xs font-semibold uppercase tracking-wide text-faint">
                <th scope="col" className="py-2.5 pr-3 text-left">Description</th>
                <th scope="col" className="py-2.5 px-3 text-right">Qty</th>
                <th scope="col" className="py-2.5 px-3 text-right">Unit price</th>
                <th scope="col" className="py-2.5 pl-3 text-right">Amount</th>
              </tr>
            </thead>
            <tbody>
              {invoice.lines.map((line) => (
                <tr key={line.id} className="border-b border-line/70 last:border-0">
                  <td className="stack-title py-3 pr-3 align-top text-ink-soft">{line.description}</td>
                  <td data-label="Qty" className="py-3 px-3 text-right align-top tnum text-muted">{line.quantity}</td>
                  <td data-label="Unit price" className="py-3 px-3 text-right align-top money text-muted">
                    {sign(line.unit_price_minor)}₹{formatAmount(Math.abs(line.unit_price_minor))}
                  </td>
                  <td data-label="Amount" className="py-3 pl-3 text-right align-top money font-semibold text-ink">
                    {sign(line.amount_minor)}₹{formatAmount(Math.abs(line.amount_minor))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* Totals — foot exactly to the total */}
        <div className="ml-auto flex w-full max-w-xs flex-col gap-2.5">
          <TotalRow label="Subtotal" value={`${sign(invoice.subtotal_minor)}₹${formatAmount(Math.abs(invoice.subtotal_minor))}`} tone="muted" />
          <TotalRow label={`Tax (GST ${invoice.tax_rate_pct}%)`} value={`${sign(invoice.tax_minor)}₹${formatAmount(Math.abs(invoice.tax_minor))}`} tone="muted" />
          <div className="border-t border-line pt-2.5">
            <TotalRow label="Total" value={`${sign(invoice.total_minor)}₹${formatAmount(Math.abs(invoice.total_minor))}`} strong />
          </div>
        </div>

        {invoice.notes ? (
          <p className="rounded-tile bg-ground px-4 py-3 text-sm text-muted">{invoice.notes}</p>
        ) : null}
      </Card>

      {/* Right — payment status & actions */}
      <Card className="flex flex-col gap-5 p-6">
        <h2 className="text-lg font-bold tracking-tight">Payment status</h2>

        {isCreditNote ? (
          <p className="text-sm text-muted">
            A credit note reduces the customer's balance. It carries no payment of its own.
          </p>
        ) : (
          <>
            <div className="flex flex-col gap-3">
              <TotalRow label="Invoice total" value={formatMoney(invoice.total_minor)} tone="muted" />
              <TotalRow
                label="Paid to date"
                value={formatMoney(invoice.amount_paid_minor)}
                tone={invoice.amount_paid_minor > 0 ? "positive" : "muted"}
              />
              <div className="border-t border-line pt-3">
                <TotalRow label="Outstanding" value={formatMoney(outstanding)} strong />
              </div>
            </div>

            {canRecord ? (
              <Can permission="finance.billing.payment.record" scope="ORG">
                <Button
                  variant="primary"
                  size="md"
                  leadingIcon={<PlusIcon className="size-4" />}
                  onClick={() => setRecording(true)}
                >
                  Record payment
                </Button>
              </Can>
            ) : (
              <div className="flex items-center gap-2 rounded-tile bg-ground px-4 py-3 text-sm font-medium text-muted">
                {invoice.status === "paid" ? (
                  <>
                    <CheckCircleIcon className="size-4 text-positive" />
                    Settled in full — nothing outstanding.
                  </>
                ) : invoice.status === "draft" ? (
                  "Draft — issue the invoice before recording a payment."
                ) : (
                  "Void — this invoice has been cancelled."
                )}
              </div>
            )}
          </>
        )}
      </Card>

      {/* Compliance sits below the document rather than beside it: the IRN, the signed QR and the
          attempt history are what a person opens this screen for when something has gone wrong,
          and none of them fit in a sidebar column. */}
      <CompliancePanel invoice={invoice} />

      {recording ? (
        <PaymentFormDialog
          invoice={invoice}
          onClose={() => setRecording(false)}
          onSaved={(payment) => {
            setRecording(false);
            toast.success(`Payment ${payment.reference} recorded.`);
            onReload();
          }}
        />
      ) : null}
    </div>
  );
}
