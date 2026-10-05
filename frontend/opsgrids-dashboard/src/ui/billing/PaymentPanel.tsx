import { Button } from "@opsgrids/ui";

import { formatMoney } from "@/lib/money";

import { PaymentNotice } from "./PaymentNotice";
import { COPY } from "./paymentCopy";
import type { NoticeCopy } from "./paymentCopy";
import { PhoneDialog } from "./PhoneDialog";
import type { PayInvoice, PayState } from "./usePayInvoice";

function noticeFor(state: PayState): NoticeCopy | null {
  switch (state.phase) {
    case "idle":
    case "needs_phone":
      return null;
    case "starting":
      return COPY.opening;
    case "open":
      return COPY.inWindow;
    case "waiting":
      return COPY.pending;
    case "captured":
      return COPY.captured;
    case "failed":
      return COPY.failed;
    case "expired":
      return COPY.expired;
    case "unconfirmed":
      return COPY.unconfirmed;
    case "closed":
      return state.failed ? COPY.closedAfterFailure : COPY.closed;
    case "refused":
      return state.notice;
  }
}

function contextFor(state: PayState): string | undefined {
  if (state.phase === "idle") return undefined;
  if ("session" in state) return `${state.target.label} · ${formatMoney(state.session.amountMinor, state.session.currency)}`;
  return state.target.label;
}

/** Where the invoice flow reports itself: the live notice, its one follow-up action, and the phone prompt. */
export function PaymentPanel({ pay }: { pay: PayInvoice }) {
  const { state } = pay;
  const canCheck = state.phase === "closed" || state.phase === "unconfirmed";

  return (
    <>
      <PaymentNotice
        copy={noticeFor(state)}
        context={contextFor(state)}
        action={
          canCheck ? (
            <Button variant="outline" size="sm" loading={pay.busy} onClick={() => void pay.checkAgain()}>
              {state.phase === "closed" ? "Check status" : "Check again"}
            </Button>
          ) : undefined
        }
      />
      {state.phase === "needs_phone" ? (
        <PhoneDialog
          open
          label={state.target.label}
          serverError={state.fieldError}
          onSubmit={(customer) => void pay.start(state.target, customer)}
          onClose={pay.cancel}
        />
      ) : null}
    </>
  );
}
