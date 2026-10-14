import { useCallback, useEffect, useMemo } from "react";
import { Link, useParams } from "react-router-dom";

import { getCustomer, getGstRegistration, getInvoice, listFilings } from "@/api/client";
import { buildTaxInvoice, documentFileTitle, isPrintable } from "@/invoice/taxInvoice";
import { TaxInvoiceDocument } from "@/invoice/TaxInvoiceDocument";
import { ApiError, problemMessage, useAsync } from "@opsgrids/client";
import { Button, ErrorState, GST_PORTAL_CONNECTED, InlineAlert, Skeleton } from "@/ui";
import { ArrowLeftIcon } from "@opsgrids/ui";

/**
 * A document is dark-on-white wherever it is read, so this page pins the light palette for as
 * long as it is mounted and hands the app's own theme back on the way out. The theme provider
 * only writes when its own state changes, so the two never fight.
 */
function useLightPalette() {
  useEffect(() => {
    const root = document.documentElement;
    const previous = root.getAttribute("data-theme");
    root.setAttribute("data-theme", "light");
    return () => {
      if (previous === null) root.removeAttribute("data-theme");
      else root.setAttribute("data-theme", previous);
    };
  }, []);
}

/** The browser offers `document.title` as the saved file's name, so the title is the file name. */
function useDocumentTitle(title: string | null) {
  useEffect(() => {
    if (title === null) return undefined;
    const previous = document.title;
    document.title = title;
    return () => {
      document.title = previous;
    };
  }, [title]);
}

/** Opens the system share sheet with a link to this document. Dismissing it is not an error. */
async function shareDocument(number: string, buyer: string): Promise<void> {
  try {
    await navigator.share({ title: documentFileTitle(number, buyer), url: window.location.href });
  } catch (error) {
    if (!(error instanceof DOMException && error.name === "AbortError")) throw error;
  }
}

function PaperSkeleton() {
  return (
    <div
      className="invoice-sheet flex flex-col gap-6 bg-surface"
      aria-busy="true"
      aria-label="Loading invoice"
    >
      <Skeleton className="h-16 w-full rounded-tile" />
      <Skeleton className="h-16 w-full rounded-tile" />
      <Skeleton className="h-48 w-full rounded-tile" />
      <Skeleton className="h-32 w-full rounded-tile" />
    </div>
  );
}

/**
 * `/invoices/:id/print` — the invoice as a document to send. No app chrome (see `main.tsx`):
 * the toolbar is screen-only, the sheet is what prints. One "Print or save as PDF" button opens the
 * browser's print dialog (where "Save as PDF" is a destination), and the page title is already
 * `INV-… - Customer` so that is the file name offered. Where the browser can share (a phone),
 * "Share" hands the link to the system share sheet.
 */
export function InvoicePrintPage() {
  const { id = "" } = useParams();
  useLightPalette();
  const canShare = typeof navigator.share === "function";

  const invoice = useAsync(useCallback(() => getInvoice(id), [id]), [id]);
  const invoiceData = invoice.status === "success" ? invoice.data : null;
  const customerId = invoiceData?.customer_id ?? null;
  const correctsId = invoiceData?.corrects_invoice_id ?? null;
  const wantsFilings = GST_PORTAL_CONNECTED && invoiceData?.einvoice?.irn != null;

  const customer = useAsync(
    useCallback(() => (customerId ? getCustomer(customerId) : Promise.resolve(null)), [customerId]),
    [customerId],
  );
  const registration = useAsync(
    useCallback(async () => {
      try {
        return await getGstRegistration();
      } catch (error) {
        // 404 is "none configured yet": a state the document reports as a gap, not a failure.
        if (error instanceof ApiError && error.status === 404) return null;
        throw error;
      }
    }, []),
    [],
  );
  const original = useAsync(
    useCallback(async () => {
      if (!correctsId) return null;
      try {
        return await getInvoice(correctsId);
      } catch {
        return null;
      }
    }, [correctsId]),
    [correctsId],
  );
  const filings = useAsync(
    useCallback(async () => {
      if (!wantsFilings) return null;
      try {
        return await listFilings(id);
      } catch {
        // The filing history sits behind its own permission. The gap list says what is missing.
        return null;
      }
    }, [id, wantsFilings]),
    [id, wantsFilings],
  );

  const failure = [invoice, customer, registration].find((s) => s.status === "error");
  const ready =
    invoice.status === "success" &&
    customer.status === "success" &&
    customer.data !== null &&
    registration.status === "success" &&
    original.status === "success" &&
    filings.status === "success";

  const built = useMemo(() => {
    if (
      invoice.status !== "success" ||
      customer.status !== "success" ||
      customer.data === null ||
      registration.status !== "success" ||
      original.status !== "success" ||
      filings.status !== "success"
    ) {
      return null;
    }
    return buildTaxInvoice({
      invoice: invoice.data,
      customer: customer.data,
      registration: registration.data,
      original: original.data,
      filings: filings.data,
      portalConnected: GST_PORTAL_CONNECTED,
    });
  }, [invoice, customer, registration, original, filings]);

  useDocumentTitle(built ? documentFileTitle(built.model.number, built.model.buyer.name) : null);

  const retry = () => {
    invoice.reload();
    customer.reload();
    registration.reload();
    original.reload();
    filings.reload();
  };

  const printable = built !== null && isPrintable(built.gaps);

  return (
    <div className="min-h-dvh bg-ground print:bg-transparent">
      <div className="print:hidden border-b border-line bg-surface">
        <div className="mx-auto flex max-w-4xl flex-wrap items-center justify-between gap-4 px-4 py-4 sm:px-6">
          <Link
            to={`/invoices/${encodeURIComponent(id)}`}
            className="inline-flex items-center gap-2 text-sm font-semibold text-muted transition-colors hover:text-ink"
          >
            <ArrowLeftIcon className="size-4" />
            Back to invoice
          </Link>
          <div className="flex flex-wrap items-center gap-3">
            {canShare && built ? (
              <Button
                variant="outline"
                onClick={() => void shareDocument(built.model.number, built.model.buyer.name)}
                disabled={!printable}
              >
                Share
              </Button>
            ) : null}
            <Button variant="primary" onClick={() => window.print()} disabled={!printable}>
              Print or save as PDF
            </Button>
          </div>
        </div>
      </div>

      <main className="mx-auto flex max-w-4xl flex-col gap-4 px-4 py-6 sm:px-6 print:max-w-none print:p-0">
        <div className="print:hidden flex flex-col gap-3" aria-live="polite">
          {built && printable ? (
            <p className="text-sm text-muted">
              To save a PDF, choose Print or save as PDF and pick &ldquo;Save as PDF&rdquo; as the
              destination. The file is named {built.model.number} - {built.model.buyer.name}.
            </p>
          ) : null}
          {built
            ? built.gaps.map((gap) => (
                <InlineAlert
                  key={`${gap.key}-${gap.message}`}
                  tone={gap.severity === "blocking" ? "danger" : "warning"}
                  title={gap.severity === "blocking" ? "Fill this in before sending" : "Check before sending"}
                  live="off"
                  action={
                    gap.fix ? (
                      <Link
                        to={gap.fix.to}
                        className="text-sm font-semibold text-accent-ink underline underline-offset-2"
                      >
                        {gap.fix.label}
                      </Link>
                    ) : gap.key === "qr_missing" || gap.key === "original_invoice" ? (
                      <Button size="sm" variant="outline" onClick={retry}>
                        Try again
                      </Button>
                    ) : undefined
                  }
                >
                  {gap.message}
                </InlineAlert>
              ))
            : null}
          {built ? (
            <InlineAlert tone="info" title="Not on this document yet" live="off">
              Finance does not store your registered address, your bank details or a unit of
              measure for each line, so none of them are printed.
            </InlineAlert>
          ) : null}
        </div>

        {failure ? (
          <ErrorState
            title="Couldn't load this invoice"
            message={problemMessage(failure.error)}
            onRetry={retry}
          />
        ) : null}
        {!failure && !ready ? <PaperSkeleton /> : null}
        {built ? <TaxInvoiceDocument model={built.model} /> : null}
      </main>
    </div>
  );
}
