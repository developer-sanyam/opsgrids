/**
 * Cashfree's hosted checkout for an OpsGrids invoice, opened as a modal over this page.
 *
 * The browser gets one thing from the server: a payment session id. Cashfree's SDK resolves
 * `checkout()` with its own account of what happened (paid, closed, redirected). That account is
 * **never proof**: the caller always asks the server — which asks Cashfree — and only that answer
 * moves the screen. It is read for one thing only, the same hint Razorpay's `ondismiss` gives: how
 * long to wait on the server. A closed modal is worth one look; a finished one, the full wait.
 */
import { loadScriptOnce } from "./script";

export const CASHFREE_SDK_SRC = "https://sdk.cashfree.com/js/v3/cashfree.js";

interface CashfreeInstance {
  checkout(options: { paymentSessionId: string; redirectTarget: "_modal" }): Promise<unknown>;
}

type CashfreeFactory = (options: { mode: "sandbox" | "production" }) => CashfreeInstance;

function cashfreeGlobal(): CashfreeFactory | undefined {
  return (window as unknown as { Cashfree?: CashfreeFactory }).Cashfree;
}

/**
 * How Cashfree's modal ended — a hint for how long to wait on the server, never what happened.
 *
 * - `completed`: the SDK reported `paymentDetails` (a payment finished, whatever its result) or a
 *   `redirect` to the return page.
 * - `closed`: anything else — `error` (the customer closed the modal, or an attempt errored), an
 *   empty result, or a rejection.
 */
export type CashfreeOutcome = "completed" | "closed";

function outcomeOf(result: unknown): CashfreeOutcome {
  if (typeof result !== "object" || result === null) return "closed";
  const r = result as Record<string, unknown>;
  if (r.error) return "closed";
  return r.paymentDetails || r.redirect ? "completed" : "closed";
}

/** Resolves when Cashfree's window closes, with how it closed. Carries nothing about the money. */
export async function openCashfree(
  action: { paymentSessionId: string; mode: "sandbox" | "production" },
  onOpened?: () => void,
): Promise<CashfreeOutcome> {
  await loadScriptOnce(CASHFREE_SDK_SRC, () => cashfreeGlobal() !== undefined, "Cashfree");
  const Cashfree = cashfreeGlobal();
  if (!Cashfree) throw new Error("Cashfree checkout did not load.");
  try {
    const opening = Cashfree({ mode: action.mode }).checkout({ paymentSessionId: action.paymentSessionId, redirectTarget: "_modal" });
    onOpened?.();
    return outcomeOf(await opening);
  } catch {
    // A rejection is the SDK's account too: a closed modal as far as this page can tell. Whether
    // money moved is the server's to say, and the caller still asks it.
    return "closed";
  }
}
