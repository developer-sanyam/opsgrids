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
}

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
  const { signal, onStatus, attempts = POLL_ATTEMPTS, intervalMs = POLL_INTERVAL_MS } = options;
  let last: CheckoutStatus | null = null;

  for (let attempt = 0; attempt < attempts; attempt += 1) {
    if (attempt > 0) await sleep(intervalMs, signal);
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
      if (error instanceof ApiError && error.status >= 400 && error.status < 500) return { kind: "refused", error };
    }
  }
  return { kind: "unsettled", last };
}
