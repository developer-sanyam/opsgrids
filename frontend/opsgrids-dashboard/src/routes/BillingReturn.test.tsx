/**
 * The page a provider's redirect lands on. Arriving proves nothing, so it asks the server about
 * the charge named in the URL — and refuses to ask about anything that is not a charge id.
 */
import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { ApiError } from "@opsgrids/client";

vi.mock("@/lib/checkout/poll", () => ({ pollCheckout: vi.fn() }));

import { PaymentContractError } from "@/api/payment";
import type { CheckoutState, CheckoutStatus } from "@/api/payment";
import { pollCheckout } from "@/lib/checkout/poll";
import type { PollOptions, PollOutcome } from "@/lib/checkout/poll";
import { checkoutStatus } from "@/test-utils/payment";

import { BillingReturnPage } from "./BillingReturn";

const CHARGE = "6f1c1d1e-0000-4000-8000-000000000001";

/** `PlatformCheckoutStatusOut` for this charge, as a Cashfree return lands on it. */
function statusOf(state: CheckoutState): CheckoutStatus {
  return checkoutStatus(state, { charge_id: CHARGE, provider: "cashfree" });
}

const STATUS = statusOf("captured");

function renderAt(url: string) {
  return render(
    <MemoryRouter initialEntries={[url]}>
      <Routes>
        <Route path="/billing/return" element={<BillingReturnPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("BillingReturnPage", () => {
  it("polls the charge in the URL and reports what the server confirmed", async () => {
    vi.mocked(pollCheckout).mockImplementation(async (_id: string, options: PollOptions) => {
      options.onStatus?.(STATUS);
      return { kind: "settled", status: STATUS };
    });
    renderAt(`/billing/return?charge_id=${CHARGE}&order_status=PAID`);

    const region = screen.getByRole("status");
    expect(await within(region).findByText("Payment received.")).toBeTruthy();
    expect(within(region).getByText("₹4,999.00")).toBeTruthy();
    expect(pollCheckout).toHaveBeenCalledWith(CHARGE, expect.objectContaining({ signal: expect.any(AbortSignal) }));
    expect(screen.getByRole("link", { name: "Back to Invoices" }).getAttribute("href")).toBe("/invoices");
  });

  it("checks neutrally while it waits, and says don't pay again once the server has seen a payment", async () => {
    let report: ((status: CheckoutStatus) => void) | undefined;
    vi.mocked(pollCheckout).mockImplementation((_id: string, options: PollOptions) => {
      report = options.onStatus;
      return new Promise(() => undefined);
    });
    renderAt(`/billing/return?charge_id=${CHARGE}`);
    // A cancelled checkout redirects here too: arriving is no sign anything was paid.
    expect(screen.getByText("Checking the payment status…")).toBeTruthy();
    act(() => report?.(statusOf("authorized")));
    expect(screen.getByText("Payment pending — don't pay again.")).toBeTruthy();
  });

  it("says don't pay again when the wait runs out on an authorized payment", async () => {
    vi.mocked(pollCheckout).mockResolvedValueOnce({ kind: "unsettled", last: statusOf("authorized") });
    renderAt(`/billing/return?charge_id=${CHARGE}`);
    expect(await screen.findByText("Still waiting for confirmation — don't pay again.")).toBeTruthy();
  });

  it("offers another check when the wait runs out, keeping it and the way back on screen while it runs", async () => {
    const user = userEvent.setup();
    vi.mocked(pollCheckout).mockResolvedValueOnce({ kind: "unsettled", last: statusOf("created") });
    renderAt(`/billing/return?charge_id=${CHARGE}`);
    expect(await screen.findByText("No payment has been confirmed yet.")).toBeTruthy();
    // The result takes focus, so a keyboard user is where the next action is.
    expect(document.activeElement?.contains(screen.getByRole("status"))).toBe(true);

    let finish: ((outcome: PollOutcome) => void) | undefined;
    vi.mocked(pollCheckout).mockReturnValueOnce(new Promise((resolve) => (finish = resolve)));
    await user.click(screen.getByRole("button", { name: "Check again" }));
    expect(screen.getByRole("button", { name: "Check again" }).getAttribute("aria-busy")).toBe("true");
    expect(screen.getByRole("link", { name: "Back to Invoices" })).toBeTruthy();

    act(() => finish?.({ kind: "settled", status: statusOf("expired") }));
    expect(await screen.findByText("This payment session expired.")).toBeTruthy();
  });

  it("never tells a customer who may have paid that nothing was charged", async () => {
    vi.mocked(pollCheckout).mockResolvedValue({ kind: "refused", error: new PaymentContractError("status") });
    renderAt(`/billing/return?charge_id=${CHARGE}`);
    expect(await screen.findByText("We couldn't confirm this payment.")).toBeTruthy();
    expect(screen.queryByText(/Nothing was charged/)).toBeNull();
  });

  it("says so when the charge is not this organisation's", async () => {
    vi.mocked(pollCheckout).mockResolvedValue({ kind: "refused", error: new ApiError(404, "Not found", { status: 404 }) });
    renderAt(`/billing/return?charge_id=${CHARGE}`);
    expect(await screen.findByText("We couldn't find this payment.")).toBeTruthy();
  });

  it.each(["/billing/return", "/billing/return?charge_id=order_123", "/billing/return?charge_id=../../admin"])(
    "asks the server nothing for %s",
    (url) => {
      vi.mocked(pollCheckout).mockClear();
      renderAt(url);
      expect(screen.getByText("There is no payment to check here")).toBeTruthy();
      expect(pollCheckout).not.toHaveBeenCalled();
    },
  );
});
