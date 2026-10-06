import { Link } from "react-router-dom";

import { useCan } from "@opsgrids/client";

/** The billing route Invoices lists through (`services/billing/app/api/routes.py`). */
const INVOICE_READ = "core.billing.invoice.read";

/**
 * The overdue line's call to action. It goes to Invoices, where the overdue invoice carries its own
 * "Pay now": subscription's dunning summary (`DunningOut`) does not name the invoice it is
 * collecting, so this line cannot open a checkout for the right one itself. When `DunningOut`
 * gains the cycle's `invoice_id`, this becomes the checkout button.
 *
 * Rendered for whoever may pay (the caller checks that). A role that may pay but not read
 * invoices would land on an access-denied page, so it is told who can pay instead.
 */
export function PayOverdueLink({ product }: { product: string }) {
  const canReadInvoices = useCan(INVOICE_READ, "ORG");
  if (!canReadInvoices) return <> Ask your organisation&apos;s owner to pay it.</>;
  return (
    <>
      {" "}
      <Link
        to="/invoices"
        className="font-semibold text-accent-ink underline underline-offset-2 hover:no-underline"
      >
        Pay now<span className="sr-only"> for {product}</span>
      </Link>
    </>
  );
}
