import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { Button, Card, EmptyState } from "@opsgrids/ui";

import { checkoutRefusal } from "@/api/payment";
import type { CheckoutStatus } from "@/api/payment";
import { pollCheckout } from "@/lib/checkout/poll";
import type { PollOutcome } from "@/lib/checkout/poll";
import { formatMoney } from "@/lib/money";
import { PageHeader } from "@/ui";
import { PaymentNotice } from "@/ui/billing/PaymentNotice";
import { COPY } from "@/ui/billing/paymentCopy";
import type { NoticeCopy } from "@/ui/billing/paymentCopy";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function noticeFor(outcome: PollOutcome | null): NoticeCopy {
  if (outcome === null || outcome.kind === "aborted") return COPY.pending;
  if (outcome.kind === "unsettled") return COPY.unconfirmed;
  if (outcome.kind === "refused") {
    const refusal = checkoutRefusal(outcome.error);
    if (refusal.kind === "not_found") {
      return { tone: "warning", title: "We couldn't find this payment.", detail: "Open Invoices to see what is still due." };
    }
    if (refusal.kind === "forbidden") {
      return { tone: "warning", title: "You don't have permission to see this payment.", detail: "Ask your organisation's owner." };
    }
    return { tone: "danger", title: refusal.kind === "other" ? refusal.message : "We couldn't check this payment." };
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
  return (
    <div className="flex flex-col gap-6">
      <PageHeader eyebrow="Billing" title="Payment" description="What the payment provider has confirmed to OpsGrids for this payment." />
      <Card className="flex flex-col gap-4 p-6">
        <PaymentNotice
          copy={noticeFor(outcome)}
          context={status ? formatMoney(status.amountMinor, status.currency) : undefined}
          action={
            outcome?.kind === "unsettled" ? (
              <Button
                variant="outline"
                size="sm"
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
        {!waiting ? (
          <Link to="/invoices" className="self-start text-sm font-semibold text-accent-ink underline underline-offset-2 hover:no-underline">
            Back to Invoices
          </Link>
        ) : null}
      </Card>
    </div>
  );
}
