/**
 * Cashfree's hosted checkout for an OpsGrids invoice, opened as a modal over this page.
 *
 * The browser gets one thing from the server: a payment session id. Cashfree's SDK resolves
 * `checkout()` with its own account of what happened (paid, closed, redirected); that result is
 * **discarded** here, unread. The caller always asks the server — which asks Cashfree — and only
 * that answer moves the screen.
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

/** Resolves when Cashfree's window closes, however it closed. Carries nothing about the payment. */
export async function openCashfree(action: { paymentSessionId: string; mode: "sandbox" | "production" }): Promise<void> {
  await loadScriptOnce(CASHFREE_SDK_SRC, () => cashfreeGlobal() !== undefined, "Cashfree");
  const Cashfree = cashfreeGlobal();
  if (!Cashfree) throw new Error("Cashfree checkout did not load.");
  try {
    await Cashfree({ mode: action.mode }).checkout({ paymentSessionId: action.paymentSessionId, redirectTarget: "_modal" });
  } catch {
    // A rejection is the SDK's account too, and is discarded the same way: whether money moved
    // is the server's to say, so the caller polls it either way.
  }
}
