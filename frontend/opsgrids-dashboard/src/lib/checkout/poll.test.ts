/**
 * The bounded status poll: every ~3 s for ~60 s of visible time, stopping on a terminal state, on a
 * 4xx, or when the screen goes away — and spending nothing while the tab is hidden.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@opsgrids/client";

vi.mock("@/api/payment", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/payment")>()),
  getCheckoutStatus: vi.fn(),
}));

import * as payment from "@/api/payment";
import type { CheckoutState, CheckoutStatus } from "@/api/payment";

import { POLL_ATTEMPTS, POLL_INTERVAL_MS, pollCheckout } from "./poll";

function status(state: CheckoutState): CheckoutStatus {
  return { chargeId: "c1", state, provider: "razorpay", amountMinor: 100, currency: "INR" };
}

let visibility: DocumentVisibilityState = "visible";

beforeEach(() => {
  vi.useFakeTimers();
  visibility = "visible";
  vi.spyOn(document, "visibilityState", "get").mockImplementation(() => visibility);
});

afterEach(() => {
  vi.useRealTimers();
});

function setVisibility(next: DocumentVisibilityState) {
  visibility = next;
  document.dispatchEvent(new Event("visibilitychange"));
}

describe("pollCheckout", () => {
  it("reads at once, then every interval, and stops on captured", async () => {
    vi.mocked(payment.getCheckoutStatus)
      .mockResolvedValueOnce(status("created"))
      .mockResolvedValueOnce(status("authorized"))
      .mockResolvedValueOnce(status("captured"));
    const seen: CheckoutState[] = [];
    const result = pollCheckout("c1", { signal: new AbortController().signal, onStatus: (s) => seen.push(s.state) });

    await vi.advanceTimersByTimeAsync(0);
    expect(payment.getCheckoutStatus).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 2);

    await expect(result).resolves.toEqual({ kind: "settled", status: status("captured") });
    expect(seen).toEqual(["created", "authorized", "captured"]);
    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 5);
    expect(payment.getCheckoutStatus).toHaveBeenCalledTimes(3);
  });

  it.each(["failed", "expired"] as const)("stops on %s", async (state) => {
    vi.mocked(payment.getCheckoutStatus).mockResolvedValue(status(state));
    const result = pollCheckout("c1", { signal: new AbortController().signal });
    await vi.advanceTimersByTimeAsync(0);
    await expect(result).resolves.toEqual({ kind: "settled", status: status(state) });
  });

  it("gives up after about a minute of reads, reporting the last state", async () => {
    vi.mocked(payment.getCheckoutStatus).mockResolvedValue(status("authorized"));
    const result = pollCheckout("c1", { signal: new AbortController().signal });
    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * POLL_ATTEMPTS);
    await expect(result).resolves.toEqual({ kind: "unsettled", last: status("authorized") });
    expect(payment.getCheckoutStatus).toHaveBeenCalledTimes(POLL_ATTEMPTS);
    expect(POLL_INTERVAL_MS * POLL_ATTEMPTS).toBe(60_000);
  });

  it("spends one read on a glance", async () => {
    vi.mocked(payment.getCheckoutStatus).mockResolvedValue(status("created"));
    const result = pollCheckout("c1", { signal: new AbortController().signal, attempts: 1 });
    await vi.advanceTimersByTimeAsync(0);
    await expect(result).resolves.toEqual({ kind: "unsettled", last: status("created") });
    expect(payment.getCheckoutStatus).toHaveBeenCalledTimes(1);
  });

  it("pauses while the tab is hidden and resumes when it is visible again", async () => {
    vi.mocked(payment.getCheckoutStatus).mockResolvedValueOnce(status("created")).mockResolvedValueOnce(status("captured"));
    const result = pollCheckout("c1", { signal: new AbortController().signal });
    await vi.advanceTimersByTimeAsync(0);
    setVisibility("hidden");
    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 30);
    expect(payment.getCheckoutStatus).toHaveBeenCalledTimes(1);

    setVisibility("visible");
    await vi.advanceTimersByTimeAsync(0);
    await expect(result).resolves.toEqual({ kind: "settled", status: status("captured") });
  });

  it("never retries a 4xx", async () => {
    const notFound = new ApiError(404, "Not found", { status: 404 });
    vi.mocked(payment.getCheckoutStatus).mockRejectedValue(notFound);
    const result = pollCheckout("c1", { signal: new AbortController().signal });
    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 3);
    await expect(result).resolves.toEqual({ kind: "refused", error: notFound });
    expect(payment.getCheckoutStatus).toHaveBeenCalledTimes(1);
  });

  it("carries on through a 5xx or a dropped connection", async () => {
    vi.mocked(payment.getCheckoutStatus)
      .mockRejectedValueOnce(new ApiError(503, "Unavailable", null))
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValueOnce(status("captured"));
    const result = pollCheckout("c1", { signal: new AbortController().signal });
    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 2);
    await expect(result).resolves.toMatchObject({ kind: "settled" });
  });

  it("stops when the screen goes away", async () => {
    vi.mocked(payment.getCheckoutStatus).mockResolvedValue(status("created"));
    const controller = new AbortController();
    const result = pollCheckout("c1", { signal: controller.signal });
    await vi.advanceTimersByTimeAsync(0);
    controller.abort();
    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 3);
    await expect(result).resolves.toEqual({ kind: "aborted" });
    expect(payment.getCheckoutStatus).toHaveBeenCalledTimes(1);
  });
});
