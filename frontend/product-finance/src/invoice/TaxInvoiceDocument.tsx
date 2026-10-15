import type { ReactNode } from "react";

import { amountInWords } from "@/lib/amountInWords";
import { formatDate, formatDateTime } from "@/lib/format";
import { formatAmount, formatMoney } from "@/lib/money";
import { QrCode } from "@opsgrids/ui";

import type { TaxInvoiceModel, TaxSummaryRow } from "./taxInvoice";

/** Trim trailing zeros: 18 -> "18", 2.5 -> "2.5". */
function rate(pct: number): string {
  return `${Number(pct.toFixed(2))}%`;
}

const QUANTITY = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 4 });

/** A labelled fact. Renders nothing for an absent value, so a missing field leaves no trace. */
function Fact({ label, children }: { label: string; children: ReactNode | null | undefined }) {
  if (children === null || children === undefined || children === "") return null;
  return (
    <div className="flex gap-2">
      <dt className="shrink-0 text-muted">{label}</dt>
      <dd className="min-w-0 break-words font-semibold text-ink">{children}</dd>
    </div>
  );
}

function Party({
  heading,
  name,
  rows,
}: {
  heading: string;
  name: ReactNode;
  rows: ReactNode;
}) {
  return (
    <section aria-label={heading} className="flex min-w-0 flex-col gap-1">
      <h2 className="text-caption font-bold uppercase tracking-wide text-muted">{heading}</h2>
      <p className="text-card-title font-bold text-ink">{name}</p>
      <dl className="flex flex-col gap-1 text-body-sm">{rows}</dl>
    </section>
  );
}

/** Column head for the numeric columns of the two tables. */
const NUM_TH = "px-2 py-2 text-right font-bold";
const NUM_TD = "px-2 py-2 text-right tnum align-top";

function SummaryTable({ model }: { model: TaxInvoiceModel }) {
  const intra = model.taxMode === "intra";
  const rows: readonly TaxSummaryRow[] = model.summary;
  return (
    <table className="stack-rows invoice-summary w-full border-collapse text-caption tnum">
      <caption className="pb-2 text-left text-caption font-bold uppercase tracking-wide text-muted">
        Tax summary
      </caption>
      <thead>
        <tr className="border-y border-ink text-ink">
          <th scope="col" className="px-2 py-2 text-left font-bold">
            {model.showHsn ? "HSN / SAC" : "Rate"}
          </th>
          <th scope="col" className={NUM_TH}>
            Taxable value
          </th>
          {model.showHsn ? (
            <th scope="col" className={NUM_TH}>
              Rate
            </th>
          ) : null}
          {intra ? (
            <>
              <th scope="col" className={NUM_TH}>
                CGST
              </th>
              <th scope="col" className={NUM_TH}>
                SGST
              </th>
            </>
          ) : (
            <th scope="col" className={NUM_TH}>
              IGST
            </th>
          )}
          <th scope="col" className={NUM_TH}>
            Total tax
          </th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={`${row.hsnSac ?? ""}|${row.taxRatePct}`} className="break-inside-avoid border-b border-line">
            <td data-label={model.showHsn ? "HSN / SAC" : "Rate"} className="px-2 py-2 text-left align-top">
              {model.showHsn ? row.hsnSac : rate(row.taxRatePct)}
            </td>
            <td data-label="Taxable value" className={NUM_TD}>{formatAmount(row.taxable)}</td>
            {model.showHsn ? <td data-label="Rate" className={NUM_TD}>{rate(row.taxRatePct)}</td> : null}
            {intra ? (
              <>
                <td data-label="CGST" className={NUM_TD}>{formatAmount(row.cgst)}</td>
                <td data-label="SGST" className={NUM_TD}>{formatAmount(row.sgst)}</td>
              </>
            ) : (
              <td data-label="IGST" className={NUM_TD}>{formatAmount(row.igst)}</td>
            )}
            <td data-label="Total tax" className={NUM_TD}>{formatAmount(row.totalTax)}</td>
          </tr>
        ))}
      </tbody>
      <tfoot>
        <tr className="border-t border-ink font-bold text-ink">
          <th scope="row" className="px-2 py-2 text-left font-bold">
            Total
          </th>
          <td data-label="Taxable value" className={NUM_TD}>{formatAmount(model.totals.taxable)}</td>
          {model.showHsn ? <td /> : null}
          {intra ? (
            <>
              <td data-label="CGST" className={NUM_TD}>{formatAmount(model.totals.cgst)}</td>
              <td data-label="SGST" className={NUM_TD}>{formatAmount(model.totals.sgst)}</td>
            </>
          ) : (
            <td data-label="IGST" className={NUM_TD}>{formatAmount(model.totals.igst)}</td>
          )}
          <td data-label="Total tax" className={NUM_TD}>{formatAmount(model.totals.totalTax)}</td>
        </tr>
      </tfoot>
    </table>
  );
}

/**
 * The invoice as it prints: a GST tax invoice (CGST Rules, rule 46) or, for a correction, a
 * credit note that names the invoice it corrects. Every fact comes from `model`; a fact the model
 * does not have is simply absent, never a dash or a placeholder.
 *
 * Rendered in the light palette whatever theme the app is in (see `InvoicePrintPage`), because a
 * document is dark-on-white wherever it is read.
 */
export function TaxInvoiceDocument({ model }: { model: TaxInvoiceModel }) {
  const intra = model.taxMode === "intra";
  const isCreditNote = model.kind === "credit_note";
  const supplierName = model.supplier.displayName;

  return (
    <article aria-label={`${model.title} ${model.number}`} className="invoice-sheet flex flex-col gap-6 print:gap-4 bg-surface text-body-sm text-ink">
      {/* Letterhead: the organization first and largest, the document identity opposite. */}
      <header className="flex flex-wrap items-start justify-between gap-4 border-b-2 border-ink pb-4">
        <div className="flex min-w-0 flex-col gap-1">
          {supplierName ? (
            <p className="display text-page-sm font-bold text-ink">{supplierName}</p>
          ) : null}
          {model.supplier.legalName ? (
            <p className="text-body-sm text-muted">{model.supplier.legalName}</p>
          ) : null}
          {model.supplier.addressLines.length > 0 ? (
            <address className="not-italic text-body-sm text-ink-soft">
              {model.supplier.addressLines.map((line) => (
                <div key={line}>{line}</div>
              ))}
            </address>
          ) : null}
          <dl className="flex flex-col gap-1 text-body-sm">
            <Fact label="GSTIN">
              {model.supplier.gstin ? <span className="tnum">{model.supplier.gstin}</span> : null}
            </Fact>
            <Fact label="State">
              {model.supplier.stateName && model.supplier.stateCode
                ? `${model.supplier.stateName} (${model.supplier.stateCode})`
                : null}
            </Fact>
            <Fact label="PAN">
              {model.supplier.pan ? <span className="tnum">{model.supplier.pan}</span> : null}
            </Fact>
          </dl>
        </div>
        <div className="flex flex-col items-end gap-2 text-right">
          <h1 className="display text-page-sm font-bold uppercase tracking-wide text-ink">
            {model.title}
          </h1>
          <p className="rounded-chip border border-ink px-2 py-1 text-caption font-bold uppercase tracking-wide text-ink">
            Original for recipient
          </p>
          {model.lifecycleMark ? (
            <p className="rounded-chip border-2 border-ink px-2 py-1 text-caption font-bold uppercase tracking-wide text-ink">
              {model.lifecycleMark}
            </p>
          ) : null}
        </div>
      </header>

      {/* Document facts. */}
      <section aria-label="Document details" className="grid grid-cols-1 gap-x-6 gap-y-1 sm:grid-cols-2">
        <dl className="flex flex-col gap-1 text-body-sm">
          <Fact label={isCreditNote ? "Credit note no." : "Invoice no."}>
            <span className="tnum">{model.number}</span>
          </Fact>
          <Fact label="Date">
            <span className="tnum">{formatDate(model.issuedDate)}</span>
          </Fact>
          {isCreditNote ? null : (
            <Fact label="Due date">
              <span className="tnum">{formatDate(model.dueDate)}</span>
            </Fact>
          )}
          {model.corrects ? (
            <Fact label="Against invoice">
              <span className="tnum">
                {model.corrects.number} dated {formatDate(model.corrects.issuedDate)}
              </span>
            </Fact>
          ) : null}
        </dl>
        <dl className="flex flex-col gap-1 text-body-sm">
          <Fact label="Place of supply">
            {model.placeOfSupply
              ? model.placeOfSupply.name
                ? `${model.placeOfSupply.name} (${model.placeOfSupply.code})`
                : model.placeOfSupply.code
              : null}
          </Fact>
          <Fact label="Supply type">{model.supplyLabel}</Fact>
          <Fact label="Tax payable on reverse charge">{model.reverseCharge ? "Yes" : "No"}</Fact>
        </dl>
      </section>

      {/* Parties. */}
      <div className="grid grid-cols-1 gap-6 border-y border-line py-4 sm:grid-cols-2">
        <Party
          heading={isCreditNote ? "Credited to" : "Billed to"}
          name={model.buyer.name}
          rows={
            <>
              {model.buyer.contact ? <div className="text-muted">Attn: {model.buyer.contact}</div> : null}
              {model.buyer.addressLines.length > 0 ? (
                <address className="not-italic text-muted">
                  {model.buyer.addressLines.map((line) => (
                    <div key={line}>{line}</div>
                  ))}
                </address>
              ) : null}
              <Fact label="GSTIN">
                {model.buyer.gstin ? <span className="tnum">{model.buyer.gstin}</span> : null}
              </Fact>
              <Fact label="State">
                {model.buyer.stateName && model.buyer.stateCode
                  ? `${model.buyer.stateName} (${model.buyer.stateCode})`
                  : null}
              </Fact>
            </>
          }
        />
        {model.shipToLines.length > 0 ? (
          <Party
            heading="Delivered to"
            name={model.buyer.name}
            rows={
              <address className="not-italic text-muted">
                {model.shipToLines.map((line) => (
                  <div key={line}>{line}</div>
                ))}
              </address>
            }
          />
        ) : null}
      </div>

      {/* Lines. The header repeats on every printed page. */}
      <table className="stack-rows invoice-lines w-full border-collapse text-caption tnum">
        <caption className="sr-only">Items on {model.number}</caption>
        <thead>
          <tr className="border-y border-ink text-ink">
            <th scope="col" className="px-2 py-2 text-left font-bold">
              #
            </th>
            <th scope="col" className="px-2 py-2 text-left font-bold">
              Description
            </th>
            {model.showHsn ? (
              <th scope="col" className="px-2 py-2 text-left font-bold">
                HSN / SAC
              </th>
            ) : null}
            <th scope="col" className={NUM_TH}>
              Qty
            </th>
            <th scope="col" className={NUM_TH}>
              Rate (₹)
            </th>
            {model.showDiscount ? (
              <th scope="col" className={NUM_TH}>
                Discount (₹)
              </th>
            ) : null}
            <th scope="col" className={NUM_TH}>
              Taxable value (₹)
            </th>
            <th scope="col" className={NUM_TH}>
              GST
            </th>
            <th scope="col" className={NUM_TH}>
              {intra ? "CGST + SGST (₹)" : "IGST (₹)"}
            </th>
            <th scope="col" className={NUM_TH}>
              Amount (₹)
            </th>
          </tr>
        </thead>
        <tbody>
          {model.lines.map((line) => (
            <tr key={line.number} className="break-inside-avoid border-b border-line">
              <td className="invoice-line-no px-2 py-2 text-left align-top">{line.number}</td>
              <td className="stack-title px-2 py-2 text-left align-top text-body-sm text-ink">{line.description}</td>
              {model.showHsn ? <td data-label="HSN / SAC" className="px-2 py-2 text-left align-top">{line.hsnSac}</td> : null}
              <td data-label="Qty" className={NUM_TD}>
                {QUANTITY.format(line.quantity)}
                {line.unit ? ` ${line.unit}` : ""}
              </td>
              <td data-label="Rate (₹)" className={NUM_TD}>{formatAmount(line.ratePerUnit)}</td>
              {model.showDiscount ? (
                <td data-label="Discount (₹)" className={NUM_TD}>{line.discount > 0 ? formatAmount(line.discount) : ""}</td>
              ) : null}
              <td data-label="Taxable value (₹)" className={NUM_TD}>{formatAmount(line.taxable)}</td>
              <td data-label="GST" className={NUM_TD}>{rate(line.taxRatePct)}</td>
              <td data-label={intra ? "CGST + SGST (₹)" : "IGST (₹)"} className={NUM_TD}>{formatAmount(intra ? line.cgst + line.sgst : line.igst)}</td>
              <td data-label="Amount (₹)" className={`${NUM_TD} font-bold`}>{formatAmount(line.lineTotal)}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {/* Tax summary and totals stay together rather than splitting across a page. */}
      <div className="flex break-inside-avoid flex-col gap-6">
        <div className="grid grid-cols-1 items-start gap-6 md:grid-cols-5">
          <div className="md:col-span-3">
            <SummaryTable model={model} />
          </div>
          <dl className="flex flex-col gap-1 text-body-sm tnum md:col-span-2">
            <div className="flex justify-between gap-4">
              <dt className="text-muted">Taxable value</dt>
              <dd className="font-semibold">{formatMoney(model.totals.taxable)}</dd>
            </div>
            {intra ? (
              <>
                <div className="flex justify-between gap-4">
                  <dt className="text-muted">CGST</dt>
                  <dd className="font-semibold">{formatMoney(model.totals.cgst)}</dd>
                </div>
                <div className="flex justify-between gap-4">
                  <dt className="text-muted">SGST</dt>
                  <dd className="font-semibold">{formatMoney(model.totals.sgst)}</dd>
                </div>
              </>
            ) : (
              <div className="flex justify-between gap-4">
                <dt className="text-muted">IGST</dt>
                <dd className="font-semibold">{formatMoney(model.totals.igst)}</dd>
              </div>
            )}
            {model.totals.roundOff !== 0 ? (
              <div className="flex justify-between gap-4">
                <dt className="text-muted">Round-off</dt>
                <dd className="font-semibold">{formatMoney(model.totals.roundOff)}</dd>
              </div>
            ) : null}
            <div className="mt-1 flex items-baseline justify-between gap-4 border-y-2 border-ink py-2">
              <dt className="text-card-title font-bold">
                {isCreditNote ? "Credit total" : "Total"}
              </dt>
              <dd className="text-section font-bold">{formatMoney(model.totals.total)}</dd>
            </div>
          </dl>
        </div>

        {model.amountInWordsMinor !== null ? (
          <p className="text-body-sm">
            <span className="text-muted">Amount in words: </span>
            <span className="font-bold text-ink">{amountInWords(model.amountInWordsMinor)}</span>
          </p>
        ) : null}
      </div>

      {model.einvoice ? (
        <section
          aria-label="E-invoice"
          className="flex break-inside-avoid flex-wrap items-start justify-between gap-4 rounded-tile border border-ink p-4"
        >
          <dl className="flex min-w-0 flex-1 flex-col gap-1 text-body-sm">
            <Fact label="IRN">
              <span className="tnum break-all text-caption">{model.einvoice.irn}</span>
            </Fact>
            <Fact label="Ack no.">
              {model.einvoice.ackNo ? <span className="tnum">{model.einvoice.ackNo}</span> : null}
            </Fact>
            <Fact label="Ack date">
              {model.einvoice.ackDate ? (
                <span className="tnum">{formatDateTime(model.einvoice.ackDate)}</span>
              ) : null}
            </Fact>
          </dl>
          {model.einvoice.qrPayload ? (
            <div className="shrink-0 bg-surface p-1">
              <QrCode
                value={model.einvoice.qrPayload}
                level="M"
                size={144}
                label={`Signed e-invoice QR code for ${model.number}`}
              />
            </div>
          ) : null}
        </section>
      ) : null}

      <div className="grid break-inside-avoid grid-cols-1 gap-6 sm:grid-cols-2">
        <section aria-label="Payment details and notes" className="flex flex-col gap-3">
          {model.supplier.bank && !isCreditNote ? (
            <div className="flex flex-col gap-1">
              <h2 className="text-caption font-bold uppercase tracking-wide text-muted">Bank details</h2>
              <dl className="flex flex-col gap-1 text-body-sm">
                <Fact label="Account name">{model.supplier.bank.account_name}</Fact>
                <Fact label="Account no.">
                  <span className="tnum">{model.supplier.bank.account_number}</span>
                </Fact>
                <Fact label="IFSC">
                  {model.supplier.bank.ifsc ? <span className="tnum">{model.supplier.bank.ifsc}</span> : null}
                </Fact>
                <Fact label="Bank">
                  {[model.supplier.bank.bank_name, model.supplier.bank.branch].filter(Boolean).join(", ")}
                </Fact>
                <Fact label="UPI">{model.supplier.bank.upi_id}</Fact>
              </dl>
            </div>
          ) : null}
          {model.notes ? (
            <div className="flex flex-col gap-1">
              <h2 className="text-caption font-bold uppercase tracking-wide text-muted">Notes</h2>
              <p className="whitespace-pre-line text-body-sm">{model.notes}</p>
            </div>
          ) : null}
        </section>
        <section aria-label="Authorised signatory" className="flex flex-col items-end justify-between gap-8 text-right">
          {supplierName ? (
            <p className="text-body-sm font-bold">For {supplierName}</p>
          ) : null}
          <p className="w-full max-w-64 border-t border-ink pt-1 text-caption text-muted">
            Authorised signatory
          </p>
        </section>
      </div>

      <footer className="invoice-footer text-caption text-muted">
        <p>
          This is a computer-generated {model.title.toLowerCase()} · {model.number}
        </p>
      </footer>
    </article>
  );
}
