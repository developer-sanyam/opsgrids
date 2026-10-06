/**
 * Waiting on the server to say what happened to a payment: a bounded poll of the checkout status
 * read — about every 3 seconds for about a minute of *visible* time.
 *
 * - **Paused on a hidden tab.** Nobody is watching, and a backgrounded tab should not keep a
 *   provider read going; the budget is not spent while hidden, and the next read happens the
 *   moment the tab is visible again.
 * - **Stops on a terminal state** (`captured`, `failed`, `expired`) — `authorized` is not one: it
 *   is a payment the server will capture, so the poll keeps going.
 * - **Never retries a 4xx.** A 403 or 404 does not fix itself. A 5xx or a dropped connection
 *   spends one attempt and the poll carries on.
 * - **A 429 or 408 ends the wait unsettled, not refused.** It says nothing about the payment, so
 *   the screen keeps saying "don't pay again" and offers a later check — it is not retried here,
 *   and it never reads as a refusal that would put Pay now back.
 */
import { ApiError } from "@opsgrids/client";

import { getCheckoutStatus, PaymentContractError, TERMINAL_STATES } from "@/api/payment";
import type { CheckoutStatus } from "@/api/payment";

export const POLL_INTERVAL_MS = 3_000;
export const POLL_ATTEMPTS = 20;

export type PollOutcome =
  | { kind: "settled"; status: CheckoutStatus }
  | { kind: "unsettled"; last: CheckoutStatus | null }
  /** A 4xx, or an answer this page cannot read — neither improves by asking again. */
  | { kind: "refused"; error: Error }
  | { kind: "aborted" };

export interface PollOptions {
  signal: AbortSignal;
  /** Every status read, terminal or not, as it lands. */
  onStatus?: ((status: CheckoutStatus) => void) | undefined;
  /** How many reads to spend. `1` is a single glance. */
  attempts?: number | undefined;
  intervalMs?: number | undefined;
  /**
   * Wait this long before the first read. A glance after the window was closed uses one interval,
   * so a UPI approval finished out of sight has a moment to reach the provider before it is read.
   */
  initialDelayMs?: number | undefined;
}

/** Busy, not refused: neither says anything about the payment, and asking again later may work. */
const NOT_NOW = new Set([408, 429]);

function sleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    if (signal.aborted) {
      resolve();
      return;
    }
    const timer = window.setTimeout(done, ms);
    function done() {
      window.clearTimeout(timer);
      signal.removeEventListener("abort", done);
      resolve();
    }
    signal.addEventListener("abort", done);
  });
}

function untilVisible(signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    if (signal.aborted || document.visibilityState !== "hidden") {
      resolve();
      return;
    }
    function done() {
      if (!signal.aborted && document.visibilityState === "hidden") return;
      document.removeEventListener("visibilitychange", done);
      signal.removeEventListener("abort", done);
      resolve();
    }
    document.addEventListener("visibilitychange", done);
    signal.addEventListener("abort", done);
  });
}

export async function pollCheckout(chargeId: string, options: PollOptions): Promise<PollOutcome> {
  const { signal, onStatus, attempts = POLL_ATTEMPTS, intervalMs = POLL_INTERVAL_MS, initialDelayMs = 0 } = options;
  let last: CheckoutStatus | null = null;

  for (let attempt = 0; attempt < attempts; attempt += 1) {
    const delay = attempt > 0 ? intervalMs : initialDelayMs;
    if (delay > 0) await sleep(delay, signal);
    await untilVisible(signal);
    if (signal.aborted) return { kind: "aborted" };
    try {
      const status = await getCheckoutStatus(chargeId, signal);
      if (signal.aborted) return { kind: "aborted" };
      last = status;
      onStatus?.(status);
      if (TERMINAL_STATES.has(status.state)) return { kind: "settled", status };
    } catch (error: unknown) {
      if (signal.aborted) return { kind: "aborted" };
      if (error instanceof PaymentContractError) return { kind: "refused", error };
      if (error instanceof ApiError && NOT_NOW.has(error.status)) return { kind: "unsettled", last };
      if (error instanceof ApiError && error.status >= 400 && error.status < 500) return { kind: "refused", error };
    }
  }
  return { kind: "unsettled", last };
}
