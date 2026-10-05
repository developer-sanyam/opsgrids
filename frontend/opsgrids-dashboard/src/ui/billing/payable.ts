import type { Invoice, Subscription } from "@/api/types";

/**
 * Which invoices get "Pay now".
 *
 * Billing records what was billed and never whether it was collected (`InvoiceAdminOut`'s
 * docstring, services/billing/app/schemas.py), so no invoice says "unpaid". The signal this page
 * does have is subscription's dunning: a subscription with an open collection cycle has an invoice
 * that failed to collect. So an invoice is offered for payment when it is issued (`finalized`),
 * is for a positive amount, and bills a subscription that is in dunning right now. The server is
 * still the judge — it answers 409 for an invoice that is already paid, and the page says so.
 *
 * `paid` is the invoices this screen has itself just seen confirmed, so their button goes away
 * without waiting for dunning to close.
 */
export function payableInvoiceIds(
  invoices: readonly Invoice[],
  subscriptions: readonly Subscription[],
  paid: ReadonlySet<string>,
): ReadonlySet<string> {
  const overdue = new Set(subscriptions.filter((s) => s.dunning !== null).map((s) => s.id));
  return new Set(
    invoices
      .filter(
        (inv) =>
          inv.state === "finalized" &&
          inv.total_minor > 0 &&
          inv.subscription_id !== null &&
          overdue.has(inv.subscription_id) &&
          !paid.has(inv.id),
      )
      .map((inv) => inv.id),
  );
}
