import { Link } from "react-router-dom";

/**
 * The overdue line's call to action. It goes to Invoices, where the overdue invoice carries its own
 * "Pay now": subscription's dunning summary (`DunningOut`) does not name the invoice it is
 * collecting, so this line cannot open a checkout for the right one itself. When `DunningOut`
 * gains the cycle's `invoice_id`, this becomes the checkout button.
 */
export function PayOverdueLink({ product }: { product: string }) {
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
