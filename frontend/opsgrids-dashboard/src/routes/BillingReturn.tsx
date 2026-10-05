import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { Button, Card, EmptyState } from "@opsgrids/ui";

import { checkoutRefusal } from "@/api/payment";
import type { CheckoutStatus } from "@/api/payment";
import { pollCheckout } from "@/lib/checkout/poll";
import type { PollOutcome } from "@/lib/checkout/poll";
import { formatMoney } from "@/lib/money";
import { PageHeader } from "@/ui";
import { focusIfLost } from "@/ui/billing/focusIfLost";
import { PaymentNotice } from "@/ui/billing/PaymentNotice";
import { COPY } from "@/ui/billing/paymentCopy";
import type { NoticeCopy } from "@/ui/billing/paymentCopy";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * Arriving here is no sign a payment was made — a cancelled checkout redirects here too — so the
 * wait is a neutral check until the server says `authorized`, and a wait that runs out says "don't
 * pay again" only for a payment the server has seen.
 */
function noticeFor(outcome: PollOutcome | null, status: CheckoutStatus | null): NoticeCopy {
  if (outcome === null || outcome.kind === "aborted") return status?.state === "authorized" ? COPY.pending : COPY.checking;
  if (outcome.kind === "unsettled") return outcome.last?.state === "authorized" ? COPY.unconfirmed : COPY.noPaymentYet;
  if (outcome.kind === "refused") {
    const refusal = checkoutRefusal(outcome.error);
    if (refusal.kind === "not_found") {
      return { tone: "warning", title: "We couldn't find this payment.", detail: "Open Invoices to see what is still due." };
    }
    if (refusal.kind === "forbidden") {
      return { tone: "warning", title: "You don't have permission to see this payment.", detail: "Ask your organisation's owner." };
    }
    // Never the create-time "nothing was charged": the customer may have just paid.
    return COPY.unreadable;
  }
  const state = outcome.status.state;
  return state === "captured" ? COPY.captured : state === "failed" ? COPY.failed : COPY.expired;
}

/**
 * Where a provider sends the customer back after a redirect flow: `/billing/return?charge_id=…`.
 *
 * Arriving here proves nothing — anyone can open this URL — so the page only asks the server what
 * happened to the charge (the same bounded poll the Pay now flow uses) and says that. It never
 * reads the provider's own query parameters, and it stores nothing.
 */
export function BillingReturnPage() {
  const [params] = useSearchParams();
  const chargeId = params.get("charge_id") ?? "";
  const valid = UUID.test(chargeId);
  const [round, setRound] = useState(0);
  const [status, setStatus] = useState<CheckoutStatus | null>(null);
  const [outcome, setOutcome] = useState<PollOutcome | null>(null);
  const noticeRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (outcome !== null) focusIfLost(noticeRef.current);
  }, [outcome]);

  useEffect(() => {
    if (!valid) return;
    const controller = new AbortController();
    void pollCheckout(chargeId, { signal: controller.signal, onStatus: setStatus }).then((result) => {
      if (!controller.signal.aborted) setOutcome(result);
    });
    return () => controller.abort();
  }, [chargeId, valid, round]);

  if (!valid) {
    return (
      <div className="flex flex-col gap-6">
        <PageHeader eyebrow="Billing" title="Payment" />
        <EmptyState
          title="There is no payment to check here"
          description="This link is missing its payment reference. Open Invoices to see what is still due."
          action={
            <Link to="/invoices" className="font-semibold text-accent-ink underline underline-offset-2 hover:no-underline">
              Go to Invoices
            </Link>
          }
        />
      </div>
    );
  }

  const waiting = outcome === null;
  // A re-check keeps its button (busy) and the way back on screen, so focus has somewhere to stay.
  const rechecking = waiting && round > 0;
  return (
    <div className="flex flex-col gap-6">
      <PageHeader eyebrow="Billing" title="Payment" description="What the payment provider has confirmed to OpsGrids for this payment." />
      <Card className="flex flex-col gap-4 p-6">
        <div ref={noticeRef} tabIndex={-1} className="focus:outline-none">
          <PaymentNotice
            copy={noticeFor(outcome, status)}
            context={status ? formatMoney(status.amountMinor, status.currency) : undefined}
            action={
              outcome?.kind === "unsettled" || rechecking ? (
                <Button
                  variant="outline"
                  size="sm"
                  loading={rechecking}
                  onClick={() => {
                    setOutcome(null);
                    setRound((n) => n + 1);
                  }}
                >
                  Check again
                </Button>
              ) : undefined
            }
          />
        </div>
        {!waiting || rechecking ? (
          <Link to="/invoices" className="self-start text-sm font-semibold text-accent-ink underline underline-offset-2 hover:no-underline">
            Back to Invoices
          </Link>
        ) : null}
      </Card>
    </div>
  );
}
