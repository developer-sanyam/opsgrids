import type { InlineAlertTone } from "@opsgrids/ui";

/** What a payment screen says, as data, so the invoice flow and the return page say it the same way. */
export interface NoticeCopy {
  tone: InlineAlertTone;
  title: string;
  detail?: string | undefined;
}

export const COPY = {
  opening: { tone: "info", title: "Opening the payment window…" },
  inWindow: { tone: "info", title: "Finish paying in the payment window." },
  pending: {
    tone: "info",
    title: "Payment pending — don't pay again.",
    detail: "We're waiting for the payment provider to confirm it. This usually takes a few seconds.",
  },
  /** A look at a payment nobody is known to have made: after a closed window, or a re-check. */
  checking: { tone: "info", title: "Checking the payment status…", detail: "Asking the payment provider what happened to this payment." },
  captured: { tone: "success", title: "Payment received.", detail: "The payment provider has confirmed it. Thank you." },
  failed: {
    tone: "danger",
    title: "The payment didn't go through.",
    detail: "You can try again, with the same method or another one.",
  },
  expired: { tone: "warning", title: "This payment session expired.", detail: "Start a new payment to pay this invoice." },
  unconfirmed: {
    tone: "warning",
    title: "Still waiting for confirmation — don't pay again.",
    detail:
      "The payment provider hasn't confirmed this payment yet. Check again in a few minutes; if it went through, it will show as received.",
  },
  /** The wait ran out on a checkout with no payment seen — by the browser or by the provider. */
  noPaymentYet: {
    tone: "warning",
    title: "No payment has been confirmed yet.",
    detail:
      "If you approved a payment in your UPI or bank app, don't pay again — it can take a few minutes to show. If you didn't finish paying, you can pay again.",
  },
  /** The status read failed in a way asking again here will not fix — after the window, so money may have moved. */
  unreadable: {
    tone: "warning",
    title: "We couldn't confirm this payment.",
    detail: "If you paid, don't pay again — it will show as received once the payment provider confirms it. Reload this page in a few minutes to check.",
  },
  unavailable: {
    tone: "warning",
    title: "Online payment isn't available right now.",
    detail: "Nothing was charged. Contact OpsGrids support to pay this invoice another way.",
  },
  closed: {
    tone: "info",
    title: "The payment window closed before the payment finished.",
    detail: "If you approved it in your UPI or bank app, it can take a minute to show — check its status before paying again.",
  },
  closedAfterFailure: {
    tone: "warning",
    title: "The payment didn't go through.",
    detail: "The payment window was closed after a failed attempt. Check its status, or pay again.",
  },

  // How `POST /payment/checkouts` says no, one entry per refusal code (api/payment.ts).

  /** 409 `invoice_already_paid` — the only refusal that marks an invoice paid. */
  alreadyPaid: {
    tone: "success",
    title: "This invoice is already paid.",
    detail: "A payment for it has already been received — there is nothing more to pay.",
  },
  /** 409 `checkout_in_progress`: another click is opening this invoice's checkout. */
  preparing: {
    tone: "info",
    title: "This payment is already being prepared.",
    detail: "Wait a moment, then select Pay now again.",
  },
  /** 409 `previous_checkout_not_closable`: the expired checkout could not be closed yet — it may still be paid. */
  previousClosing: {
    tone: "warning",
    title: "The last payment window for this invoice is still being closed.",
    detail: "Try again in a minute. If you paid in it, don't pay again — it will show as received once the payment provider confirms it.",
  },
  /** 409 `previous_checkout_payment_in_flight`: a payment on an earlier checkout is still processing. */
  previousInFlight: {
    tone: "warning",
    title: "An earlier payment for this invoice is still being processed.",
    detail: "Wait a few minutes before trying again — if that payment goes through, there will be nothing more to pay.",
  },
  currencyUnsupported: {
    tone: "warning",
    title: "Online payment isn't available for this invoice's currency yet.",
    detail: "Contact OpsGrids support to pay it another way.",
  },
  belowMinimum: {
    tone: "warning",
    title: "This amount is below the payment provider's minimum.",
    detail: "Contact OpsGrids support to settle it another way.",
  },
  nothingToPay: {
    tone: "info",
    title: "There's nothing to pay on this invoice.",
    detail: "Reload the page to see its current state.",
  },
} satisfies Record<string, NoticeCopy>;
