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
