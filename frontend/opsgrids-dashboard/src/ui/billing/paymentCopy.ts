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
} satisfies Record<string, NoticeCopy>;
