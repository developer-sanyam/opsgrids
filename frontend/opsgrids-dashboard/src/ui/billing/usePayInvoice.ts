/**
 * The "Pay now" flow for one invoice at a time: open a checkout on the server, open the provider's
 * window, then wait on the server's status read — the only thing that ever says "paid".
 *
 * - **One payment at a time, and a double click opens one window.** The guard is a ref, not
 *   state: two clicks inside one frame would both read a stale `idle`.
 * - **Nothing is persisted.** The checkout lives here, in memory, for as long as the screen does;
 *   leaving the screen aborts the wait and forgets it.
 * - **"Don't pay again" is said only when a payment may exist**: the provider's window reported
 *   one finishing, or the server said `authorized`. A window closed with nothing seen gets a
 *   neutral look at the server, and paying again stays one click away.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { checkoutRefusal, createCheckout } from "@/api/payment";
import type { BusyCode, CheckoutCustomer, CheckoutRefusal, CheckoutSession, CheckoutState, UnpayableCode } from "@/api/payment";
import { launchCheckout } from "@/lib/checkout/launch";
import { pollCheckout, POLL_INTERVAL_MS } from "@/lib/checkout/poll";

import { COPY } from "./paymentCopy";
import type { NoticeCopy } from "./paymentCopy";

export interface PayTarget {
  invoiceId: string;
  /** "Invoice INV-0042" — shown to the customer here and in the provider's window. */
  label: string;
}

/**
 * - `full`: a payment may be under way (the window reported one finishing) — "don't pay again".
 * - `check`: a look at a checkout nobody is known to have paid — after a closed window, or a
 *   re-check. It becomes `full` the moment the server says `authorized`.
 */
export type WaitMode = "full" | "check";

export type PayState =
  | { phase: "idle" }
  /**
   * `withPhone`: this attempt carries the number typed into the phone prompt, which stays up.
   * `session`: the checkout, once the server has opened it, while its window loads.
   */
  | { phase: "starting"; target: PayTarget; withPhone: boolean; session?: CheckoutSession | undefined }
  | { phase: "needs_phone"; target: PayTarget; fieldError?: string | undefined }
  | { phase: "open"; target: PayTarget; session: CheckoutSession }
  | { phase: "waiting"; target: PayTarget; session: CheckoutSession; state: CheckoutState | null; mode: WaitMode }
  | { phase: "captured" | "failed" | "expired"; target: PayTarget; session: CheckoutSession }
  /**
   * The wait ran out without an answer. `sawPayment`: the window reported a payment finishing, or
   * the server last said `authorized` — paying again then risks paying twice.
   */
  | { phase: "unconfirmed"; target: PayTarget; session: CheckoutSession; sawPayment: boolean }
  | { phase: "closed"; target: PayTarget; session: CheckoutSession; failed: boolean }
  /** `session`: set when the refusal came after a checkout was opened — its window failed, or its status could not be read. */
  | { phase: "refused"; target: PayTarget; notice: NoticeCopy; session?: CheckoutSession | undefined };

export interface PayInvoice {
  state: PayState;
  /** True while a payment is being opened, is open, or is being waited on. */
  busy: boolean;
  /** Invoices the server has said, on this screen, are paid — `paid: true` here, or a 409 `invoice_already_paid`. */
  paid: ReadonlySet<string>;
  /**
   * The invoice whose last checkout should be checked before anything else is done with it — its
   * row offers "Check status", not "Pay now".
   */
  checkingId: string | null;
  /** Whether "Pay again" is offered: the last checkout closed, or ran out, with no payment seen. */
  canPayAgain: boolean;
  start: (target: PayTarget, customer?: CheckoutCustomer) => Promise<void>;
  /** Ask the server again — after the window closed early, or the wait ran out. */
  checkAgain: () => Promise<void>;
  /** Open a new payment window for the invoice whose last one closed without a payment. */
  payAgain: () => Promise<void>;
  /** Close the phone prompt without paying. */
  cancel: () => void;
}

/** `CheckoutCustomerIn.phone`, as the framework's own 422 names it. */
const PHONE_FIELD = "customer.phone";

const BUSY_COPY: Record<BusyCode, NoticeCopy> = {
  checkout_in_progress: COPY.preparing,
  previous_checkout_not_closable: COPY.previousClosing,
  previous_checkout_payment_in_flight: COPY.previousInFlight,
};

const UNPAYABLE_COPY: Record<UnpayableCode, NoticeCopy> = {
  checkout_not_supported: COPY.unavailable,
  currency_unsupported: COPY.currencyUnsupported,
  amount_below_minimum: COPY.belowMinimum,
  nothing_to_pay: COPY.nothingToPay,
};

function refusalState(target: PayTarget, refusal: CheckoutRefusal, hadCustomer: boolean): PayState {
  switch (refusal.kind) {
    case "already_paid":
      return { phase: "refused", target, notice: COPY.alreadyPaid };
    case "busy":
      // Not "paid": wait, then try again. The button stays, and the server decides the next click.
      return { phase: "refused", target, notice: BUSY_COPY[refusal.code] };
    case "unpayable":
      return { phase: "refused", target, notice: UNPAYABLE_COPY[refusal.code] };
    case "needs_details":
      // The prompt collects the phone and sends the signed-in person's name and email with it. A
      // refusal of what it sent is shown on its field.
      return { phase: "needs_phone", target, fieldError: hadCustomer ? refusal.message : undefined };
    case "conflict":
      // A 409 with no code this page knows. Never "paid": the server's own sentence, and the button stays.
      return { phase: "refused", target, notice: { tone: "warning", title: refusal.message } };
    case "not_found":
      return {
        phase: "refused",
        target,
        notice: { tone: "warning", title: "We couldn't find this invoice.", detail: "It may have been voided. Reload the page to see its current state." },
      };
    case "forbidden":
      return {
        phase: "refused",
        target,
        notice: {
          tone: "warning",
          title: "You don't have permission to pay invoices.",
          detail: "Ask your organisation's owner to pay it.",
        },
      };
    case "invalid": {
      const phoneError = refusal.fields[PHONE_FIELD];
      if (hadCustomer && phoneError) return { phase: "needs_phone", target, fieldError: phoneError };
      return { phase: "refused", target, notice: { tone: "danger", title: refusal.message } };
    }
    case "other":
      return { phase: "refused", target, notice: { tone: "danger", title: refusal.message } };
  }
}

/** How long a wait is worth, by what the browser saw — and, for a re-check, what was seen before. */
type Follow = { kind: "full" } | { kind: "glance"; failed: boolean } | { kind: "recheck"; sawPayment: boolean };

function mayPayAgain(state: PayState): boolean {
  return state.phase === "closed" || (state.phase === "unconfirmed" && !state.sawPayment);
}

function checkingIdOf(state: PayState): string | null {
  if (state.phase === "closed" || state.phase === "unconfirmed") return state.target.invoiceId;
  if (state.phase === "waiting" && state.mode === "check") return state.target.invoiceId;
  return null;
}

export function usePayInvoice(): PayInvoice {
  const [state, setState] = useState<PayState>({ phase: "idle" });
  const [paid, setPaid] = useState<ReadonlySet<string>>(new Set());
  const busyRef = useRef(false);
  const [busy, setBusy] = useState(false);
  const controllerRef = useRef<AbortController | null>(null);
  const stateRef = useRef<PayState>(state);
  useEffect(() => {
    stateRef.current = state;
  }, [state]);

  useEffect(() => () => controllerRef.current?.abort(), []);

  const claim = useCallback((): AbortController | null => {
    if (busyRef.current) return null;
    busyRef.current = true;
    setBusy(true);
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    return controller;
  }, []);

  const markPaid = useCallback((invoiceId: string) => {
    setPaid((prev) => new Set(prev).add(invoiceId));
  }, []);

  /** Show a refusal; only a 409 `invoice_already_paid` is remembered as paid. */
  const refuse = useCallback(
    (target: PayTarget, refusal: CheckoutRefusal, hadCustomer: boolean) => {
      if (refusal.kind === "already_paid") markPaid(target.invoiceId);
      setState(refusalState(target, refusal, hadCustomer));
    },
    [markPaid],
  );

  const release = useCallback(() => {
    busyRef.current = false;
    setBusy(false);
  }, []);

  /**
   * Wait on the server. A glance spends one read, one interval after the window closed; a read of
   * `authorized` on it turns it into the full wait, because a payment now exists.
   */
  const follow = useCallback(
    async (target: PayTarget, session: CheckoutSession, signal: AbortSignal, how: Follow) => {
      let mode: WaitMode = how.kind === "full" ? "full" : "check";
      let sawPayment = how.kind === "full" || (how.kind === "recheck" && how.sawPayment);
      setState({ phase: "waiting", target, session, state: null, mode });
      const onStatus = (status: { state: CheckoutState }) =>
        setState({ phase: "waiting", target, session, state: status.state, mode });
      let outcome =
        how.kind === "glance"
          ? await pollCheckout(session.chargeId, { signal, onStatus, attempts: 1, initialDelayMs: POLL_INTERVAL_MS })
          : await pollCheckout(session.chargeId, { signal, onStatus });
      if (how.kind === "glance" && outcome.kind === "unsettled" && outcome.last?.state === "authorized") {
        mode = "full";
        sawPayment = true;
        outcome = await pollCheckout(session.chargeId, { signal, onStatus });
      }
      if (signal.aborted || outcome.kind === "aborted") return;
      if (outcome.kind === "settled") {
        const settled = outcome.status.state;
        // `paid`: the server's own word, true only for a captured payment.
        if (outcome.status.paid) markPaid(target.invoiceId);
        setState({ phase: settled === "captured" ? "captured" : settled === "failed" ? "failed" : "expired", target, session });
        return;
      }
      if (outcome.kind === "refused") {
        // After the window, so money may have moved: never the create-time "nothing was charged".
        setState({ phase: "refused", target, notice: COPY.unreadable, session });
        return;
      }
      if (how.kind === "glance" && mode === "check") {
        setState({ phase: "closed", target, session, failed: how.failed });
        return;
      }
      setState({ phase: "unconfirmed", target, session, sawPayment: sawPayment || outcome.last?.state === "authorized" });
    },
    [markPaid],
  );

  const start = useCallback(
    async (target: PayTarget, customer?: CheckoutCustomer) => {
      const controller = claim();
      if (!controller) return;
      const { signal } = controller;
      try {
        setState({ phase: "starting", target, withPhone: customer !== undefined });
        let session: CheckoutSession;
        try {
          session = await createCheckout(target.invoiceId, customer);
        } catch (error: unknown) {
          if (!signal.aborted) refuse(target, checkoutRefusal(error), customer !== undefined);
          return;
        }
        if (signal.aborted) return;
        if (!session.openable) {
          // A stub provider's stand-in Order: no real window would take it.
          setState({ phase: "refused", target, notice: COPY.unavailable });
          return;
        }
        // The checkout exists from here on, so what it is (its amount, test mode) shows while its window loads.
        setState({ phase: "starting", target, withPhone: customer !== undefined, session });
        let launched;
        try {
          launched = await launchCheckout(session, target.label, () => {
            if (!signal.aborted) setState({ phase: "open", target, session });
          });
        } catch (error: unknown) {
          if (!signal.aborted) {
            const title = error instanceof Error ? error.message : "The payment window could not be opened.";
            setState({ phase: "refused", target, notice: { tone: "danger", title }, session });
          }
          return;
        }
        if (signal.aborted) return;
        await follow(target, session, signal, launched.wait === "glance" ? { kind: "glance", failed: launched.failed } : { kind: "full" });
      } finally {
        if (controllerRef.current === controller) release();
      }
    },
    [claim, follow, refuse, release],
  );

  const checkAgain = useCallback(async () => {
    const current = stateRef.current;
    if (current.phase !== "closed" && current.phase !== "unconfirmed") return;
    const controller = claim();
    if (!controller) return;
    try {
      const sawPayment = current.phase === "unconfirmed" && current.sawPayment;
      await follow(current.target, current.session, controller.signal, { kind: "recheck", sawPayment });
    } finally {
      if (controllerRef.current === controller) release();
    }
  }, [claim, follow, release]);

  const payAgain = useCallback(async () => {
    const current = stateRef.current;
    if ((current.phase === "closed" || current.phase === "unconfirmed") && mayPayAgain(current)) await start(current.target);
  }, [start]);

  const cancel = useCallback(() => {
    if (stateRef.current.phase === "needs_phone") setState({ phase: "idle" });
  }, []);

  return {
    state,
    busy,
    paid,
    checkingId: checkingIdOf(state),
    canPayAgain: mayPayAgain(state),
    start,
    checkAgain,
    payAgain,
    cancel,
  };
}
