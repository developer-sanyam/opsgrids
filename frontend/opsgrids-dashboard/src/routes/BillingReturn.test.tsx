/**
 * The page a provider's redirect lands on. Arriving proves nothing, so it asks the server about
 * the charge named in the URL — and refuses to ask about anything that is not a charge id.
 */
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { ApiError } from "@opsgrids/client";

vi.mock("@/lib/checkout/poll", () => ({ pollCheckout: vi.fn() }));

import type { CheckoutStatus } from "@/api/payment";
import { pollCheckout } from "@/lib/checkout/poll";
import type { PollOptions } from "@/lib/checkout/poll";

import { BillingReturnPage } from "./BillingReturn";

const CHARGE = "6f1c1d1e-0000-4000-8000-000000000001";

const STATUS: CheckoutStatus = { chargeId: CHARGE, state: "captured", provider: "cashfree", amountMinor: 499900, currency: "INR" };

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

  it("says don't pay again while it waits", () => {
    vi.mocked(pollCheckout).mockReturnValue(new Promise(() => undefined));
    renderAt(`/billing/return?charge_id=${CHARGE}`);
    expect(screen.getByText("Payment pending — don't pay again.")).toBeTruthy();
  });

  it("offers another check when the wait runs out", async () => {
    const user = userEvent.setup();
    vi.mocked(pollCheckout).mockResolvedValueOnce({ kind: "unsettled", last: null });
    renderAt(`/billing/return?charge_id=${CHARGE}`);
    expect(await screen.findByText("Still waiting for confirmation — don't pay again.")).toBeTruthy();

    vi.mocked(pollCheckout).mockResolvedValueOnce({ kind: "settled", status: { ...STATUS, state: "expired" } });
    await user.click(screen.getByRole("button", { name: "Check again" }));
    expect(await screen.findByText("This payment session expired.")).toBeTruthy();
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
