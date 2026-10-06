/**
 * "Pay now" on the Invoices page: who sees it, which invoices carry it, and how each answer the
 * server can give — paid, busy, unknown, refused with a code, details needed — reaches the
 * customer. Every answer is payment-service's own wire (libs/contracts/openapi/payment.json and the
 * coded refusals of `POST /payment/checkouts`), mapped by the page's own `fromWire`. The provider
 * window is the launcher's (tested on its own); here it is a stand-in.
 */
import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, PermissionProvider } from "@opsgrids/client";

vi.mock("@opsgrids/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@opsgrids/client")>()),
  useCurrentUser: () => ({ name: "Asha Rao", email: "asha@example.com", initials: "AR", roleDisplay: null }),
}));

vi.mock("@/api", () => ({
  listInvoices: vi.fn(),
  listSubscriptions: vi.fn(),
  getOrganization: vi.fn(),
  getCreditBalance: vi.fn(),
}));

vi.mock("@/api/payment", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/payment")>()),
  createCheckout: vi.fn(),
  getCheckoutStatus: vi.fn(),
}));

vi.mock("@/lib/checkout/launch", () => ({ launchCheckout: vi.fn() }));

import * as api from "@/api";
import * as payment from "@/api/payment";
import type { OpenableCheckout } from "@/api/payment";
import { launchCheckout } from "@/lib/checkout/launch";
import type { LaunchOutcome } from "@/lib/checkout/launch";
import { POLL_ATTEMPTS, POLL_INTERVAL_MS } from "@/lib/checkout/poll";
import {
  checkoutProblem,
  checkoutStatus as status,
  frameworkProblem,
  RAZORPAY_CHECKOUT as SESSION,
  refusalMessage,
  STUB_CHECKOUT,
} from "@/test-utils/payment";

import { InvoicesPage } from "./Invoices";

const PAY = "core.product.activation.write:ORG";

const invoice = {
  subscription_id: "sub-overdue",
  state: "finalized",
  currency: "INR",
  total_minor: 499900,
  period_start: "2026-09-01T00:00:00Z",
  period_end: "2026-10-01T00:00:00Z",
  finalized_at: "2026-10-01T00:00:00Z",
  voided_at: null,
  lines: [],
};

const INVOICES = [
  { ...invoice, id: "inv-due", number: "INV-0042" },
  { ...invoice, id: "inv-void", number: "INV-0041", state: "void" },
  { ...invoice, id: "inv-zero", number: "INV-0040", total_minor: 0 },
  { ...invoice, id: "inv-current", number: "INV-0039", subscription_id: "sub-current" },
];

const dunning = {
  cycle_id: "cy1",
  opened_at: "2026-10-01T06:00:00Z",
  grace_ends_at: "2026-10-15T00:00:00Z",
  next_attempt_at: null,
  attempts_made: 1,
  max_attempts: 4,
  amount_minor: 499900,
  currency: "INR",
};

const SUBSCRIPTIONS = [
  { id: "sub-overdue", product_key: "operations", state: "past_due", dunning },
  { id: "sub-current", product_key: "crm", state: "active", dunning: null },
];

/** The stand-in provider window: opens at once, then ends as the test says. */
function windowThat(outcome: LaunchOutcome) {
  return async (_session: OpenableCheckout, _label: string, onOpened?: () => void) => {
    onOpened?.();
    return outcome;
  };
}

function renderPage(grants: string[] = [PAY]) {
  return render(
    <PermissionProvider grants={grants}>
      <InvoicesPage />
    </PermissionProvider>,
  );
}

function liveRegion(): HTMLElement {
  const region = document.querySelector<HTMLElement>('[aria-live="polite"]');
  if (!region) throw new Error("no live region");
  return region;
}

function focusIsOnNotice(): boolean {
  return document.activeElement?.contains(liveRegion()) === true;
}

beforeEach(() => {
  vi.mocked(api.listInvoices).mockResolvedValue(INVOICES as never);
  vi.mocked(api.listSubscriptions).mockResolvedValue(SUBSCRIPTIONS as never);
  vi.mocked(api.getOrganization).mockResolvedValue({ currency: "INR" } as never);
  vi.mocked(api.getCreditBalance).mockResolvedValue({ balance_minor: 0, currency: "INR" } as never);
  vi.mocked(payment.createCheckout).mockResolvedValue(SESSION);
  vi.mocked(payment.getCheckoutStatus).mockResolvedValue(status("captured"));
  vi.mocked(launchCheckout).mockImplementation(windowThat({ wait: "full" }));
});

afterEach(() => {
  vi.useRealTimers();
  delete (window as { matchMedia?: unknown }).matchMedia;
});

describe("InvoicesPage — who is offered Pay now", () => {
  it("offers it only on the issued, positive invoice of a subscription in dunning", async () => {
    renderPage();
    expect(await screen.findByRole("button", { name: "Pay now invoice INV-0042" })).toBeTruthy();
    expect(screen.getAllByRole("button", { name: /^Pay now/ })).toHaveLength(1);
  });

  it("offers it on the one invoice the dunning cycle is collecting, not on every past one", async () => {
    const months = [
      { ...invoice, id: "inv-oct", number: "INV-0110", finalized_at: "2026-10-01T00:00:00Z" },
      { ...invoice, id: "inv-sep", number: "INV-0109", finalized_at: "2026-09-01T00:00:00Z" },
      { ...invoice, id: "inv-aug", number: "INV-0108", finalized_at: "2026-08-01T00:00:00Z" },
      // An upgrade's different total, issued before the cycle opened: not what the cycle is owed.
      { ...invoice, id: "inv-proration", number: "INV-0111", total_minor: 1200, finalized_at: "2026-10-01T03:00:00Z" },
      // Issued after the cycle opened: not the invoice that failed to collect.
      { ...invoice, id: "inv-nov", number: "INV-0112", finalized_at: "2026-11-01T00:00:00Z" },
    ];
    vi.mocked(api.listInvoices).mockResolvedValue(months as never);
    renderPage();
    expect(await screen.findByRole("button", { name: "Pay now invoice INV-0110" })).toBeTruthy();
    expect(screen.getAllByRole("button", { name: /^Pay now/ })).toHaveLength(1);
  });

  it("offers nothing without the subscribe permission, and never asks for subscriptions", async () => {
    renderPage(["core.product.activation.read:ORG"]);
    expect(await screen.findByText("INV-0042")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Pay now/ })).toBeNull();
    expect(api.listSubscriptions).not.toHaveBeenCalled();
  });

  it("offers nothing when the dunning read fails, and still lists the invoices", async () => {
    vi.mocked(api.listSubscriptions).mockRejectedValue(new ApiError(503, "down", null));
    renderPage();
    expect(await screen.findByText("INV-0042")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Pay now/ })).toBeNull();
  });
});

describe("InvoicesPage — paying", () => {
  it("opens one checkout, waits on the server, and says received only when the server does", async () => {
    const user = userEvent.setup();
    renderPage();
    const region = liveRegion();
    expect(region.textContent).toBe("");

    await user.click(await screen.findByRole("button", { name: "Pay now invoice INV-0042" }));

    expect(await within(region).findByText("Payment received.")).toBeTruthy();
    expect(within(region).getByText("Invoice INV-0042 · ₹4,999.00")).toBeTruthy();
    expect(payment.createCheckout).toHaveBeenCalledExactlyOnceWith("inv-due", undefined);
    expect(launchCheckout).toHaveBeenCalledExactlyOnceWith(SESSION, "Invoice INV-0042", expect.any(Function));
    expect(payment.getCheckoutStatus).toHaveBeenCalledWith(SESSION.chargeId, expect.any(AbortSignal));
    // Paid on this screen: its button goes away without waiting for dunning to close — and the
    // keyboard lands on what happened, not at the top of the page.
    expect(screen.queryByRole("button", { name: /Pay now/ })).toBeNull();
    expect(focusIsOnNotice()).toBe(true);
  });

  it("says the window is open only once the provider has opened it", async () => {
    const user = userEvent.setup();
    let opened: (() => void) | undefined;
    vi.mocked(launchCheckout).mockImplementation((_s, _l, onOpened) => {
      opened = onOpened;
      return new Promise(() => undefined);
    });
    renderPage();
    await user.click(await screen.findByRole("button", { name: "Pay now invoice INV-0042" }));
    expect(await within(liveRegion()).findByText("Opening the payment window…")).toBeTruthy();
    act(() => opened?.());
    expect(within(liveRegion()).getByText("Finish paying in the payment window.")).toBeTruthy();
  });

  it("opens the provider window once for a double click", async () => {
    const user = userEvent.setup();
    let release: (() => void) | undefined;
    vi.mocked(launchCheckout).mockImplementation((_s, _l, onOpened) => {
      onOpened?.();
      return new Promise((resolve) => (release = () => resolve({ wait: "full" })));
    });
    renderPage();
    const button = await screen.findByRole("button", { name: "Pay now invoice INV-0042" });
    await user.dblClick(button);
    await screen.findByText("Finish paying in the payment window.");
    expect(payment.createCheckout).toHaveBeenCalledTimes(1);
    expect(launchCheckout).toHaveBeenCalledTimes(1);
    release?.();
    expect(await screen.findByText("Payment received.")).toBeTruthy();
  });

  it("tells the customer not to pay again while a finished payment is unconfirmed", async () => {
    const user = userEvent.setup();
    vi.mocked(payment.getCheckoutStatus).mockReturnValue(new Promise(() => undefined));
    renderPage();
    await user.click(await screen.findByRole("button", { name: "Pay now invoice INV-0042" }));
    expect(await within(liveRegion()).findByText("Payment pending — don't pay again.")).toBeTruthy();
  });

  it("says the payment failed when the server says so", async () => {
    const user = userEvent.setup();
    vi.mocked(payment.getCheckoutStatus).mockResolvedValue(status("failed"));
    renderPage();
    await user.click(await screen.findByRole("button", { name: "Pay now invoice INV-0042" }));
    expect(await screen.findByText("The payment didn't go through.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Pay now invoice INV-0042" })).toBeTruthy();
  });

  it("launches nothing on a stub provider's checkout (client_action.openable: false)", async () => {
    const user = userEvent.setup();
    vi.mocked(payment.createCheckout).mockResolvedValue(STUB_CHECKOUT);
    renderPage();
    await user.click(await screen.findByRole("button", { name: "Pay now invoice INV-0042" }));
    expect(await screen.findByText("Online payment isn't available right now.")).toBeTruthy();
    expect(launchCheckout).not.toHaveBeenCalled();
    expect(payment.getCheckoutStatus).not.toHaveBeenCalled();
  });

  it("never says nothing was charged when the status read fails after the window", async () => {
    const user = userEvent.setup();
    vi.mocked(payment.getCheckoutStatus).mockRejectedValue(new payment.PaymentContractError("status"));
    renderPage();
    await user.click(await screen.findByRole("button", { name: "Pay now invoice INV-0042" }));
    expect(await screen.findByText("We couldn't confirm this payment.")).toBeTruthy();
    expect(screen.queryByText(/Nothing was charged/)).toBeNull();
  });

  it("explains a blocked payment window", async () => {
    const user = userEvent.setup();
    vi.mocked(launchCheckout).mockRejectedValue(new Error("Razorpay's payment window could not be opened."));
    renderPage();
    await user.click(await screen.findByRole("button", { name: "Pay now invoice INV-0042" }));
    expect(await screen.findByText("Razorpay's payment window could not be opened.")).toBeTruthy();
  });
});

describe("InvoicesPage — a window closed before paying", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });

  function setupUser() {
    return userEvent.setup({ advanceTimers: (ms) => vi.advanceTimersByTime(ms) });
  }

  it("looks once, a moment later, without saying don't pay again, then offers a check and paying again", async () => {
    const user = setupUser();
    vi.mocked(launchCheckout).mockImplementation(windowThat({ wait: "glance", failed: false }));
    vi.mocked(payment.getCheckoutStatus).mockResolvedValue(status("created"));
    renderPage();
    await user.click(await screen.findByRole("button", { name: "Pay now invoice INV-0042" }));

    expect(await within(liveRegion()).findByText("Checking the payment status…")).toBeTruthy();
    expect(within(liveRegion()).queryByText(/don't pay again/)).toBeNull();
    expect(payment.getCheckoutStatus).not.toHaveBeenCalled();

    await act(() => vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS));
    expect(await screen.findByText("The payment window closed before the payment finished.")).toBeTruthy();
    expect(payment.getCheckoutStatus).toHaveBeenCalledTimes(1);

    // The row the customer is on checks that payment first; it does not offer a second one.
    expect(screen.queryByRole("button", { name: "Pay now invoice INV-0042" })).toBeNull();
    expect(screen.getByRole("button", { name: "Check status of invoice INV-0042" })).toBeTruthy();

    vi.mocked(launchCheckout).mockImplementation(windowThat({ wait: "full" }));
    vi.mocked(payment.getCheckoutStatus).mockResolvedValue(status("captured"));
    await user.click(screen.getByRole("button", { name: "Pay again" }));
    expect(await screen.findByText("Payment received.")).toBeTruthy();
    expect(payment.createCheckout).toHaveBeenCalledTimes(2);
  });

  it("turns a look that finds an authorized payment into the full wait", async () => {
    const user = setupUser();
    vi.mocked(launchCheckout).mockImplementation(windowThat({ wait: "glance", failed: false }));
    vi.mocked(payment.getCheckoutStatus)
      .mockResolvedValueOnce(status("authorized"))
      .mockResolvedValueOnce(status("authorized"))
      .mockResolvedValue(status("captured"));
    renderPage();
    await user.click(await screen.findByRole("button", { name: "Pay now invoice INV-0042" }));
    await act(() => vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS));
    expect(await within(liveRegion()).findByText("Payment pending — don't pay again.")).toBeTruthy();
    await act(() => vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 2));
    expect(await screen.findByText("Payment received.")).toBeTruthy();
  });

  it("checks status from the row, keeping the check on screen while it runs, and says plainly when nothing was paid", async () => {
    const user = setupUser();
    vi.mocked(launchCheckout).mockImplementation(windowThat({ wait: "glance", failed: false }));
    vi.mocked(payment.getCheckoutStatus).mockResolvedValue(status("created"));
    renderPage();
    await user.click(await screen.findByRole("button", { name: "Pay now invoice INV-0042" }));
    await act(() => vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS));
    await screen.findByText("The payment window closed before the payment finished.");

    await user.click(screen.getByRole("button", { name: "Check status of invoice INV-0042" }));
    expect(screen.getByRole("button", { name: "Check status" }).getAttribute("aria-busy")).toBe("true");
    expect(within(liveRegion()).queryByText(/don't pay again/)).toBeNull();

    await act(() => vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * POLL_ATTEMPTS));
    expect(await screen.findByText("No payment has been confirmed yet.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Check again" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Pay again" })).toBeTruthy();
  });

  it("after a finished payment runs out the wait, says don't pay again and offers no second payment", async () => {
    const user = setupUser();
    vi.mocked(payment.getCheckoutStatus).mockResolvedValue(status("created"));
    renderPage();
    await user.click(await screen.findByRole("button", { name: "Pay now invoice INV-0042" }));
    await act(() => vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * POLL_ATTEMPTS));
    expect(await screen.findByText("Still waiting for confirmation — don't pay again.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Pay again" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Pay now invoice INV-0042" })).toBeNull();

    vi.mocked(payment.getCheckoutStatus).mockResolvedValue(status("captured"));
    await user.click(screen.getByRole("button", { name: "Check again" }));
    expect(await screen.findByText("Payment received.")).toBeTruthy();
  });
});

describe("InvoicesPage — refusals", () => {
  async function refusedWith(error: ApiError) {
    const user = userEvent.setup();
    vi.mocked(payment.createCheckout).mockRejectedValue(error);
    renderPage();
    await user.click(await screen.findByRole("button", { name: "Pay now invoice INV-0042" }));
    return user;
  }

  it.each([
    ["checkout_in_progress", "This payment is already being prepared."],
    ["previous_checkout_not_closable", "The last payment window for this invoice is still being closed."],
    ["previous_checkout_payment_in_flight", "An earlier payment for this invoice is still being processed."],
  ] as const)("409 %s: tells the customer to wait and retry, keeps the invoice payable — never 'already paid'", async (code, title) => {
    await refusedWith(checkoutProblem(code));
    expect(await within(liveRegion()).findByText(title)).toBeTruthy();
    expect(screen.queryByText("This invoice is already paid.")).toBeNull();
    expect(screen.getByRole("button", { name: "Pay now invoice INV-0042" })).toBeTruthy();
    expect(launchCheckout).not.toHaveBeenCalled();
  });

  it("409 invoice_already_paid: says the invoice is already paid and drops its button", async () => {
    await refusedWith(checkoutProblem("invoice_already_paid"));
    expect(await screen.findByText("This invoice is already paid.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Pay now/ })).toBeNull();
    expect(launchCheckout).not.toHaveBeenCalled();
  });

  it("an uncoded 409 shows the server's own sentence and is never read as paid, even this one", async () => {
    await refusedWith(new ApiError(409, "Conflict", { type: "https://errors.opsgrids.dev/conflict", status: 409, detail: "This invoice has already been paid" }));
    expect(await screen.findByText("This invoice has already been paid")).toBeTruthy();
    expect(screen.queryByText("This invoice is already paid.")).toBeNull();
    expect(screen.getByRole("button", { name: "Pay now invoice INV-0042" })).toBeTruthy();
  });

  it.each([
    ["checkout_not_supported", "Online payment isn't available right now."],
    ["currency_unsupported", "Online payment isn't available for this invoice's currency yet."],
    ["amount_below_minimum", "This amount is below the payment provider's minimum."],
    ["nothing_to_pay", "There's nothing to pay on this invoice."],
  ] as const)("%s: says why in this page's words, and nothing is opened or marked paid", async (code, title) => {
    await refusedWith(checkoutProblem(code));
    expect(await within(liveRegion()).findByText(title)).toBeTruthy();
    expect(screen.queryByText("This invoice is already paid.")).toBeNull();
    expect(screen.getByRole("button", { name: "Pay now invoice INV-0042" })).toBeTruthy();
    expect(launchCheckout).not.toHaveBeenCalled();
  });

  it("404: says the invoice could not be found", async () => {
    await refusedWith(new ApiError(404, "Not found", { status: 404, detail: "No payable charge for this invoice" }));
    expect(await screen.findByText("We couldn't find this invoice.")).toBeTruthy();
    expect(launchCheckout).not.toHaveBeenCalled();
  });

  it.each(["phone_required", "customer_details_required"] as const)(
    "422 %s: opens the details prompt and retries with the customer",
    async (code) => {
      const user = userEvent.setup();
      vi.mocked(payment.createCheckout).mockRejectedValueOnce(checkoutProblem(code)).mockResolvedValueOnce(SESSION);
      renderPage();
      await user.click(await screen.findByRole("button", { name: "Pay now invoice INV-0042" }));

      const dialog = await screen.findByRole("dialog", { name: "Add a mobile number" });
      const field = within(dialog).getByLabelText(/Mobile number/);
      expect(document.activeElement).toBe(field);
      // Nothing has been tried with a number yet: no reason under the field.
      expect(within(dialog).queryByText(refusalMessage(code))).toBeNull();
      await user.type(field, "12345");
      await user.click(within(dialog).getByRole("button", { name: "Continue to payment" }));
      expect(await within(dialog).findByText("Enter a 10-digit mobile number, like 98200 11122.")).toBeTruthy();
      expect(payment.createCheckout).toHaveBeenCalledTimes(1);

      await user.clear(field);
      await user.type(field, "+91 98200 11122");
      await user.click(within(dialog).getByRole("button", { name: "Continue to payment" }));

      expect(await screen.findByText("Payment received.")).toBeTruthy();
      expect(payment.createCheckout).toHaveBeenLastCalledWith("inv-due", {
        name: "Asha Rao",
        email: "asha@example.com",
        phone: "9820011122",
      });
    },
  );

  it("keeps the number and the dialog when the provider turns the number down, and clears the reason on edit", async () => {
    const user = userEvent.setup();
    const phoneRequired = checkoutProblem("phone_required");
    let refuse: ((error: unknown) => void) | undefined;
    vi.mocked(payment.createCheckout)
      .mockRejectedValueOnce(phoneRequired)
      .mockImplementationOnce(() => new Promise((_resolve, reject) => (refuse = reject)));
    renderPage();
    await user.click(await screen.findByRole("button", { name: "Pay now invoice INV-0042" }));
    const dialog = await screen.findByRole("dialog", { name: "Add a mobile number" });
    const field = within(dialog).getByLabelText<HTMLInputElement>(/Mobile number/);
    await user.type(field, "98200 11122");
    await user.click(within(dialog).getByRole("button", { name: "Continue to payment" }));

    // Still up, busy, while the number is tried.
    expect(within(dialog).getByRole("button", { name: "Opening payment…" }).getAttribute("aria-busy")).toBe("true");
    act(() => refuse?.(phoneRequired));

    expect(await within(dialog).findByText(refusalMessage("phone_required"))).toBeTruthy();
    expect(screen.getByRole("dialog", { name: "Add a mobile number" })).toBe(dialog);
    expect(field.value).toBe("98200 11122");
    await user.type(field, "3");
    expect(within(dialog).queryByText(refusalMessage("phone_required"))).toBeNull();
  });

  it("shows the framework's own 422 on customer.phone under the field", async () => {
    const user = userEvent.setup();
    const tooLong = frameworkProblem("customer.phone", "string_too_long", "String should have at most 16 characters");
    vi.mocked(payment.createCheckout).mockRejectedValueOnce(checkoutProblem("phone_required")).mockRejectedValueOnce(tooLong);
    renderPage();
    await user.click(await screen.findByRole("button", { name: "Pay now invoice INV-0042" }));
    const dialog = await screen.findByRole("dialog", { name: "Add a mobile number" });
    await user.type(within(dialog).getByLabelText(/Mobile number/), "98200 11122");
    await user.click(within(dialog).getByRole("button", { name: "Continue to payment" }));
    expect(await within(dialog).findByText("String should have at most 16 characters")).toBeTruthy();
  });

  it("cancelling the details prompt puts focus on the notice, not the page body", async () => {
    const user = await refusedWith(checkoutProblem("phone_required"));
    const dialog = await screen.findByRole("dialog", { name: "Add a mobile number" });
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).not.toBe(document.body);
  });
});

describe("InvoicesPage — at phone width", () => {
  it("puts Pay now on the invoice's card and it still pays", async () => {
    window.matchMedia = ((query: string) => ({
      matches: false,
      media: query,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
    })) as unknown as typeof window.matchMedia;
    const user = userEvent.setup();
    renderPage();
    expect(screen.queryByRole("table")).toBeNull();
    await user.click(await screen.findByRole("button", { name: "Pay now invoice INV-0042" }));
    expect(await screen.findByText("Payment received.")).toBeTruthy();
  });
});
