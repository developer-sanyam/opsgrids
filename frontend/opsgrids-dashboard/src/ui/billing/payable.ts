import type { Dunning, Invoice, Subscription } from "@/api/types";

/**
 * Which invoices get "Pay now" — at most **one per subscription in dunning**.
 *
 * Billing records what was billed and never whether it was collected (`InvoiceAdminOut`'s
 * docstring, services/billing/app/schemas.py), so no invoice says "unpaid", and every past invoice
 * of a subscription stays `finalized`. The signal this page does have is subscription's dunning: an
 * open collection cycle means one invoice failed to collect. `DunningOut` does not name it, so it
 * is picked here:
 *
 * - issued (`finalized`, with a `finalized_at`), for a positive amount, billing that subscription;
 * - issued no later than the cycle opened (`dunning.opened_at`) — a cycle opens on a failed
 *   collection of an invoice that already exists;
 * - of those, the ones whose total and currency are the cycle's `amount_minor` / `currency` when
 *   it carries them, else all of them; and of those, the latest issued.
 *
 * If none qualifies, the subscription offers nothing rather than a guess. The server is still the
 * judge of what is owed. `paid` is the invoices this screen has itself seen paid; it is applied
 * after the pick, so hiding the overdue invoice never promotes an older, settled one.
 */
export function payableInvoiceIds(
  invoices: readonly Invoice[],
  subscriptions: readonly Subscription[],
  paid: ReadonlySet<string>,
): ReadonlySet<string> {
  const ids = new Set<string>();
  for (const subscription of subscriptions) {
    if (subscription.dunning === null) continue;
    const overdue = overdueInvoice(invoices, subscription.id, subscription.dunning);
    if (overdue && !paid.has(overdue.id)) ids.add(overdue.id);
  }
  return ids;
}

function issuedAt(invoice: Invoice): number {
  return invoice.finalized_at === null ? Number.NaN : Date.parse(invoice.finalized_at);
}

function overdueInvoice(invoices: readonly Invoice[], subscriptionId: string, dunning: Dunning): Invoice | undefined {
  const opened = Date.parse(dunning.opened_at);
  const issued = invoices.filter(
    (inv) =>
      inv.subscription_id === subscriptionId &&
      inv.state === "finalized" &&
      inv.total_minor > 0 &&
      issuedAt(inv) <= opened,
  );
  const matching =
    dunning.amount_minor === null
      ? []
      : issued.filter((inv) => inv.total_minor === dunning.amount_minor && inv.currency === dunning.currency);
  const pool = matching.length > 0 ? matching : issued;
  return pool.reduce<Invoice | undefined>((latest, inv) => (latest === undefined || issuedAt(inv) > issuedAt(latest) ? inv : latest), undefined);
}
