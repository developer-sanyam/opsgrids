import { useState } from "react";

import {
  cancelOrderPayment,
  getOrderPayments,
  refundOrderPayment,
  startOrderCheckout,
  syncOrderPayment,
} from "@/api/client";
import type { OrderCollectionOut, OrderOut } from "@/api/types";
import { formatDate, formatMoney } from "@/lib/format";
import { minorToInput, parseMinor } from "@/lib/money";
import { openRazorpayCheckout } from "@/lib/razorpayCheckout";
import { Can, problemMessage, useAsync, useCan, useMutation } from "@opsgrids/client";
import {
  Button,
  Card,
  CollectionStatusPill,
  ConfirmDialog,
  ErrorState,
  InlineAlert,
  SectionHeader,
  Skeleton,
  TextArea,
  TextField,
} from "@/ui";
import { StatusBadge, useToast } from "@opsgrids/ui";

const TERM_LABEL: Record<string, string> = {
  prepaid: "Prepaid",
  cash: "Paid before handover",
  credit: "Credit terms",
};

/** A collection can be asked about while the provider may still move it. */
function canCheck(c: OrderCollectionOut): boolean {
  return c.state === "created" || c.state === "authorized" || c.state === "failed";
}

function canCancel(c: OrderCollectionOut): boolean {
  return c.state === "created" || c.state === "failed";
}

function refundable(c: OrderCollectionOut): number {
  return c.state === "captured" ? Math.max(c.amount_minor - c.refunded_minor, 0) : 0;
}

/**
 * The order's payment (ADR-0024 A4, Fulfilment F10): whether it is paid, every online payment
 * attempt, and — when the order can be paid online — "Collect online payment", which opens
 * Razorpay's own hosted Checkout. This screen never marks an order paid: when Checkout closes it
 * asks the server to check with Razorpay, and the order is paid only when Razorpay says so (that
 * check, or Razorpay's signed webhook). The Checkout callback's own payment data is never used.
 */
export function OrderPaymentCard({ order, onOrderChanged }: { order: OrderOut; onOrderChanged: () => void }) {
  const toast = useToast();
  const canRefund = useCan("operations.orders.payment.refund", "ORG");
  const state = useAsync(() => getOrderPayments(order.id), [order.id, order.version]);
  const [notice, setNotice] = useState<string | null>(null);
  const [refunding, setRefunding] = useState<OrderCollectionOut | null>(null);
  const [refundAmount, setRefundAmount] = useState("");
  const [refundReason, setRefundReason] = useState("");
  const [refundKey, setRefundKey] = useState("");
  const [refundError, setRefundError] = useState<string | undefined>(undefined);

  function refreshEverything() {
    state.reload();
    onOrderChanged();
  }

  const collect = useMutation(
    async () => {
      setNotice(null);
      const opened = await startOrderCheckout(order.id);
      if (!opened.checkout) {
        setNotice("The payment is still being prepared with the provider. Try again in a moment.");
        return opened;
      }
      if (!opened.checkout.openable) {
        setNotice(
          "Online payments are running without a provider connection on this platform, so this checkout cannot be paid yet.",
        );
        return opened;
      }
      await openRazorpayCheckout(opened.checkout, { businessName: "Order payment" });
      // However Checkout closed, ask the provider (through the server) what really happened.
      const checked = await syncOrderPayment(order.id, opened.collection.id);
      if (checked.state === "captured") toast.success("Payment received — confirmed with the provider.");
      else if (checked.state === "authorized") toast.success("Payment authorized — waiting for the provider to capture it.");
      return opened;
    },
    { onSuccess: () => refreshEverything(), onError: () => refreshEverything() },
  );

  const check = useMutation((chargeId: string) => syncOrderPayment(order.id, chargeId), {
    onSuccess: (checked) => {
      toast.success(checked.state === "captured" ? "Paid — confirmed with the provider." : "Status checked with the provider.");
      refreshEverything();
    },
  });

  const cancel = useMutation((chargeId: string) => cancelOrderPayment(order.id, chargeId), {
    onSuccess: () => {
      toast.success("Online payment cancelled.");
      refreshEverything();
    },
    onError: () => refreshEverything(),
  });

  const refund = useMutation(
    (input: { chargeId: string; amount: number; reason: string; key: string }) =>
      refundOrderPayment(order.id, input.chargeId, { amount_minor: input.amount, reason: input.reason }, input.key),
    {
      onSuccess: () => {
        setRefunding(null);
        toast.success("Refund requested with the provider.");
        refreshEverything();
      },
    },
  );

  function openRefund(c: OrderCollectionOut) {
    setRefunding(c);
    setRefundAmount(minorToInput(refundable(c)));
    setRefundReason("");
    setRefundError(undefined);
    // One key per refund the operator means to make: a retry of *this* dialog is the same refund.
    setRefundKey(crypto.randomUUID());
    refund.reset();
  }

  function submitRefund() {
    if (!refunding) return;
    const parsed = parseMinor(refundAmount, { required: true });
    if (!parsed.ok) {
      setRefundError(parsed.message);
      return;
    }
    if (parsed.minor <= 0 || parsed.minor > refundable(refunding)) {
      setRefundError(`Refund between 0.01 and ${formatMoney(refundable(refunding), refunding.currency)}.`);
      return;
    }
    if (refundReason.trim().length < 1) {
      setRefundError("Say why this refund is being made.");
      return;
    }
    setRefundError(undefined);
    void refund.mutate({ chargeId: refunding.id, amount: parsed.minor, reason: refundReason.trim(), key: refundKey });
  }

  if (state.status === "loading") {
    return (
      <Card className="p-6 flex flex-col gap-4">
        <SectionHeader title="Payment" />
        <Skeleton className="h-20 w-full rounded-tile" />
      </Card>
    );
  }
  if (state.status === "error") {
    return (
      <Card className="p-6 flex flex-col gap-4">
        <SectionHeader title="Payment" />
        <ErrorState message={problemMessage(state.error)} onRetry={state.reload} />
      </Card>
    );
  }

  const payments = state.data;
  const paidOnline = payments.paid_at !== null && payments.paid_via_charge_id !== null;
  const mutationError = collect.error ?? check.error ?? cancel.error;

  return (
    <Card className="p-6 flex flex-col gap-4">
      <SectionHeader
        title="Payment"
        subtitle={`${TERM_LABEL[payments.payment_term] ?? payments.payment_term} · ${formatMoney(payments.total_minor, payments.currency)}`}
      />

      {payments.paid_at ? (
        <InlineAlert tone="success" title={paidOnline ? "Paid online" : "Paid"}>
          {paidOnline
            ? `Confirmed by the payment provider on ${formatDate(payments.paid_at.slice(0, 10))}.`
            : `Payment confirmed on ${formatDate(payments.paid_at.slice(0, 10))}.`}
        </InlineAlert>
      ) : null}

      {payments.mode === "test" && payments.online_payments_configured ? (
        <div className="flex items-center gap-2 text-xs text-muted">
          <StatusBadge tone="warning" label="Test mode" />
          No real money moves.
        </div>
      ) : null}

      {notice ? <InlineAlert tone="warning" title={notice} /> : null}
      {mutationError ? <InlineAlert tone="danger" title={problemMessage(mutationError)} /> : null}

      {payments.can_collect ? (
        <Can permission="operations.orders.payment.collect" scope="ORG">
          <Button loading={collect.pending} onClick={() => void collect.mutate(undefined)}>
            Collect online payment
          </Button>
        </Can>
      ) : !payments.paid_at && payments.collect_blocked_reason ? (
        <p className="text-sm text-muted">{payments.collect_blocked_reason}</p>
      ) : null}

      {payments.collections.length > 0 ? (
        <ul className="flex flex-col gap-3" aria-label="Online payment attempts">
          {payments.collections.map((c) => (
            <li key={c.id} className="rounded-tile border border-line p-3 flex flex-col gap-2">
              <div className="flex items-center justify-between gap-3">
                <CollectionStatusPill state={c.state} />
                <span className="tnum text-sm font-semibold text-ink">{formatMoney(c.amount_minor, c.currency)}</span>
              </div>
              <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
                <dt className="text-muted">Started</dt>
                <dd className="tnum text-ink-soft">{formatDate(c.created_at.slice(0, 10))}</dd>
                {c.provider_payment_id ? (
                  <>
                    <dt className="text-muted">Payment id</dt>
                    <dd className="font-mono text-ink-soft break-all">{c.provider_payment_id}</dd>
                  </>
                ) : null}
                {c.refunded_minor > 0 ? (
                  <>
                    <dt className="text-muted">Refunded</dt>
                    <dd className="tnum text-ink-soft">{formatMoney(c.refunded_minor, c.currency)}</dd>
                  </>
                ) : null}
                {c.state === "failed" && c.failure_reason ? (
                  <>
                    <dt className="text-muted">Last attempt</dt>
                    <dd className="text-ink-soft">{c.failure_reason}</dd>
                  </>
                ) : null}
              </dl>
              <div className="flex flex-wrap gap-2">
                {canCheck(c) ? (
                  <Can permission="operations.orders.payment.collect" scope="ORG">
                    <Button size="sm" variant="outline" loading={check.pending} onClick={() => void check.mutate(c.id)}>
                      Check status
                    </Button>
                  </Can>
                ) : null}
                {canCancel(c) ? (
                  <Can permission="operations.orders.payment.collect" scope="ORG">
                    <Button size="sm" variant="outline" loading={cancel.pending} onClick={() => void cancel.mutate(c.id)}>
                      Cancel
                    </Button>
                  </Can>
                ) : null}
                {canRefund && refundable(c) > 0 ? (
                  <Button size="sm" variant="outline" onClick={() => openRefund(c)}>
                    Refund
                  </Button>
                ) : null}
              </div>
            </li>
          ))}
        </ul>
      ) : null}

      <ConfirmDialog
        open={refunding !== null}
        onClose={() => setRefunding(null)}
        onConfirm={submitRefund}
        title="Refund this payment?"
        body="The money goes back to the customer through the payment provider. A full refund makes the order unpaid again."
        confirmLabel="Send refund"
        pendingLabel="Refunding…"
        tone="danger"
        pending={refund.pending}
        {...(refund.error ? { error: problemMessage(refund.error) } : {})}
      >
        <div className="flex flex-col gap-3">
          <TextField
            label={`Amount (${refunding?.currency ?? order.currency})`}
            inputMode="decimal"
            value={refundAmount}
            onChange={(e) => setRefundAmount(e.target.value)}
            {...(refundError !== undefined ? { error: refundError } : {})}
          />
          <TextArea label="Reason" value={refundReason} onChange={(e) => setRefundReason(e.target.value)} maxLength={200} />
        </div>
      </ConfirmDialog>
    </Card>
  );
}
