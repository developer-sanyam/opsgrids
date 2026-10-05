/**
 * The "Pay now" flow for one invoice at a time: open a checkout on the server, open the provider's
 * window, then wait on the server's status read — the only thing that ever says "paid".
 *
 * - **One payment at a time, and a double click opens one window.** The guard is a ref, not
 *   state: two clicks inside one frame would both read a stale `idle`.
 * - **Nothing is persisted.** The checkout lives here, in memory, for as long as the screen does;
 *   leaving the screen aborts the wait and forgets it.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { checkoutRefusal, createCheckout } from "@/api/payment";
import type { CheckoutCustomer, CheckoutRefusal, CheckoutSession, CheckoutState } from "@/api/payment";
import { launchCheckout } from "@/lib/checkout/launch";
import { pollCheckout, POLL_ATTEMPTS } from "@/lib/checkout/poll";

import type { NoticeCopy } from "./paymentCopy";

export interface PayTarget {
  invoiceId: string;
  /** "Invoice INV-0042" — shown to the customer here and in the provider's window. */
  label: string;
}

export type PayState =
  | { phase: "idle" }
  | { phase: "starting"; target: PayTarget }
  | { phase: "needs_phone"; target: PayTarget; fieldError?: string | undefined }
  | { phase: "open"; target: PayTarget; session: CheckoutSession }
  | { phase: "waiting"; target: PayTarget; session: CheckoutSession; state: CheckoutState | null }
  | { phase: "captured" | "failed" | "expired" | "unconfirmed"; target: PayTarget; session: CheckoutSession }
  | { phase: "closed"; target: PayTarget; session: CheckoutSession; failed: boolean }
  | { phase: "refused"; target: PayTarget; notice: NoticeCopy };

export interface PayInvoice {
  state: PayState;
  /** True while a payment is being opened, is open, or is being waited on. */
  busy: boolean;
  /** Invoices the server has said, on this screen, are paid — captured here, or a 409. */
  paid: ReadonlySet<string>;
  start: (target: PayTarget, customer?: CheckoutCustomer) => Promise<void>;
  /** Ask the server again — after the window closed early, or the wait ran out. */
  checkAgain: () => Promise<void>;
  /** Close the phone prompt without paying. */
  cancel: () => void;
}

const PHONE_FIELDS = ["customer.phone", "phone"];

function refusalState(target: PayTarget, refusal: CheckoutRefusal, hadCustomer: boolean): PayState {
  switch (refusal.kind) {
    case "already_paid":
      return {
        phase: "refused",
        target,
        notice: { tone: "success", title: "This invoice is already paid.", detail: "A payment for it has already been received — there is nothing more to pay." },
      };
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
    case "coded":
      if (refusal.code === "phone_required") {
        return { phase: "needs_phone", target, fieldError: hadCustomer ? refusal.message : undefined };
      }
      return {
        phase: "refused",
        target,
        notice:
          refusal.code === "currency_unsupported"
            ? { tone: "warning", title: "Online payment isn't available for this invoice's currency yet.", detail: "Contact OpsGrids support to pay it another way." }
            : { tone: "warning", title: "This amount is below the payment provider's minimum.", detail: "Contact OpsGrids support to settle it another way." },
      };
    case "invalid": {
      const phoneError = PHONE_FIELDS.map((field) => refusal.fields[field]).find((message) => message !== undefined);
      if (hadCustomer && phoneError) return { phase: "needs_phone", target, fieldError: phoneError };
      return { phase: "refused", target, notice: { tone: "danger", title: refusal.message } };
    }
    case "other":
      return { phase: "refused", target, notice: { tone: "danger", title: refusal.message } };
  }
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

  /** Show a refusal; a 409 is the server saying the invoice is paid, so it is remembered as such. */
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

  /** Wait on the server. `glance` spends one read; anything still moving then gets the full wait. */
  const follow = useCallback(
    async (target: PayTarget, session: CheckoutSession, signal: AbortSignal, glance: { failed: boolean } | null) => {
      setState({ phase: "waiting", target, session, state: null });
      const onStatus = (status: { state: CheckoutState }) =>
        setState({ phase: "waiting", target, session, state: status.state });
      let outcome = await pollCheckout(session.chargeId, { signal, onStatus, attempts: glance ? 1 : POLL_ATTEMPTS });
      if (glance && outcome.kind === "unsettled" && outcome.last?.state === "authorized") {
        outcome = await pollCheckout(session.chargeId, { signal, onStatus });
      }
      if (signal.aborted || outcome.kind === "aborted") return;
      if (outcome.kind === "settled") {
        const settled = outcome.status.state;
        if (settled === "captured") markPaid(target.invoiceId);
        setState({ phase: settled === "captured" ? "captured" : settled === "failed" ? "failed" : "expired", target, session });
        return;
      }
      if (outcome.kind === "refused") {
        refuse(target, checkoutRefusal(outcome.error), false);
        return;
      }
      setState(glance ? { phase: "closed", target, session, failed: glance.failed } : { phase: "unconfirmed", target, session });
    },
    [markPaid, refuse],
  );

  const start = useCallback(
    async (target: PayTarget, customer?: CheckoutCustomer) => {
      const controller = claim();
      if (!controller) return;
      const { signal } = controller;
      try {
        setState({ phase: "starting", target });
        let session: CheckoutSession;
        try {
          session = await createCheckout(target.invoiceId, customer);
        } catch (error: unknown) {
          if (!signal.aborted) refuse(target, checkoutRefusal(error), customer !== undefined);
          return;
        }
        if (signal.aborted) return;
        setState({ phase: "open", target, session });
        let launched;
        try {
          launched = await launchCheckout(session, target.label);
        } catch (error: unknown) {
          if (!signal.aborted) {
            const title = error instanceof Error ? error.message : "The payment window could not be opened.";
            setState({ phase: "refused", target, notice: { tone: "danger", title } });
          }
          return;
        }
        if (signal.aborted) return;
        await follow(target, session, signal, launched.wait === "glance" ? { failed: launched.failed } : null);
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
      await follow(current.target, current.session, controller.signal, null);
    } finally {
      if (controllerRef.current === controller) release();
    }
  }, [claim, follow, release]);

  const cancel = useCallback(() => {
    if (stateRef.current.phase === "needs_phone") setState({ phase: "idle" });
  }, []);

  return { state, busy, paid, start, checkAgain, cancel };
}
