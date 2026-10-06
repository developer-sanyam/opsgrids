import { useEffect, useRef } from "react";

import { Button } from "@opsgrids/ui";

import type { CheckoutSession } from "@/api/payment";
import { formatMoney } from "@/lib/money";

import { focusIfLost } from "./focusIfLost";
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
      return state.mode === "full" || state.state === "authorized" ? COPY.pending : COPY.checking;
    case "captured":
      return COPY.captured;
    case "failed":
      return COPY.failed;
    case "expired":
      return COPY.expired;
    case "unconfirmed":
      return state.sawPayment ? COPY.unconfirmed : COPY.noPaymentYet;
    case "closed":
      return state.failed ? COPY.closedAfterFailure : COPY.closed;
    case "refused":
      return state.notice;
  }
}

/** The checkout this state is about, once the server has opened one. */
function sessionOf(state: PayState): CheckoutSession | null {
  return "session" in state && state.session ? state.session : null;
}

function contextFor(state: PayState): string | undefined {
  if (state.phase === "idle") return undefined;
  const session = sessionOf(state);
  if (session) return `${state.target.label} · ${formatMoney(session.amountMinor, session.currency)}`;
  return state.target.label;
}

/**
 * The states that end a step and leave the person something to read or do. When one lands and the
 * control they were on has gone (disabled, removed, or a provider window that took focus away),
 * focus moves to the notice — `idle` included, for a phone prompt cancelled from a disabled row.
 */
const LANDINGS: ReadonlySet<PayState["phase"]> = new Set([
  "idle",
  "captured",
  "failed",
  "expired",
  "unconfirmed",
  "closed",
  "refused",
]);

/** Where the invoice flow reports itself: the live notice, its follow-up actions, and the phone prompt. */
export function PaymentPanel({ pay }: { pay: PayInvoice }) {
  const { state } = pay;
  const noticeRef = useRef<HTMLDivElement>(null);
  const previous = useRef(state);

  useEffect(() => {
    // Only on a change of state — never on mount, which would take focus from the page itself.
    if (previous.current === state) return;
    previous.current = state;
    if (LANDINGS.has(state.phase)) focusIfLost(noticeRef.current);
  }, [state]);

  // The check stays on screen, busy, while it runs: a button that vanishes under the keyboard
  // drops focus to the top of the page.
  const checking = state.phase === "waiting" && state.mode === "check";
  const canCheck = state.phase === "closed" || state.phase === "unconfirmed" || checking;
  const showPhone = state.phase === "needs_phone" || (state.phase === "starting" && state.withPhone);

  return (
    <>
      <div ref={noticeRef} tabIndex={-1} className="focus:outline-none">
        <PaymentNotice
          copy={noticeFor(state)}
          context={contextFor(state)}
          testMode={sessionOf(state)?.testMode === true}
          action={
            canCheck ? (
              <div className="flex flex-wrap gap-2">
                <Button variant="outline" size="sm" loading={pay.busy} onClick={() => void pay.checkAgain()}>
                  {state.phase === "unconfirmed" ? "Check again" : "Check status"}
                </Button>
                {pay.canPayAgain ? (
                  <Button variant="ghost" size="sm" disabled={pay.busy} onClick={() => void pay.payAgain()}>
                    Pay again
                  </Button>
                ) : null}
              </div>
            ) : undefined
          }
        />
      </div>
      {showPhone ? (
        <PhoneDialog
          open
          label={state.target.label}
          pending={state.phase === "starting"}
          serverError={state.phase === "needs_phone" ? state.fieldError : undefined}
          onSubmit={(customer) => void pay.start(state.target, customer)}
          onClose={pay.cancel}
        />
      ) : null}
    </>
  );
}
