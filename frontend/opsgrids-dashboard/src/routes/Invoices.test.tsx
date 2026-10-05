/**
 * "Pay now" on the Invoices page: who sees it, which invoices carry it, and how each answer the
 * server can give — paid, unknown, refused with a code, phone needed — reaches the customer. The
 * provider window is the launcher's (tested on its own); here it is a stand-in.
 */
import { render, screen, within } from "@testing-library/react";
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
import type { CheckoutSession } from "@/api/payment";
import { launchCheckout } from "@/lib/checkout/launch";

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
  opened_at: "2026-10-01T00:00:00Z",
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

const SESSION: CheckoutSession = {
  chargeId: "charge-1",
  provider: "razorpay",
  action: { type: "razorpay_checkout", keyId: "rzp_test_Platform0001", orderId: "order_P1" },
  amountMinor: 499900,
  currency: "INR",
  expiresAt: null,
};

function captured() {
  return { chargeId: "charge-1", state: "captured" as const, provider: "razorpay" as const, amountMinor: 499900, currency: "INR" };
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

beforeEach(() => {
  vi.mocked(api.listInvoices).mockResolvedValue(INVOICES as never);
  vi.mocked(api.listSubscriptions).mockResolvedValue(SUBSCRIPTIONS as never);
  vi.mocked(api.getOrganization).mockResolvedValue({ currency: "INR" } as never);
  vi.mocked(api.getCreditBalance).mockResolvedValue({ balance_minor: 0, currency: "INR" } as never);
  vi.mocked(payment.createCheckout).mockResolvedValue(SESSION);
  vi.mocked(payment.getCheckoutStatus).mockResolvedValue(captured());
  vi.mocked(launchCheckout).mockResolvedValue({ wait: "full" });
});

afterEach(() => {
  delete (window as { matchMedia?: unknown }).matchMedia;
});

describe("InvoicesPage — who is offered Pay now", () => {
  it("offers it only on the issued, positive invoice of a subscription in dunning", async () => {
    renderPage();
    expect(await screen.findByRole("button", { name: "Pay now invoice INV-0042" })).toBeTruthy();
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
    expect(launchCheckout).toHaveBeenCalledExactlyOnceWith(SESSION, "Invoice INV-0042");
    expect(payment.getCheckoutStatus).toHaveBeenCalledWith("charge-1", expect.any(AbortSignal));
    // Paid on this screen: its button goes away without waiting for dunning to close.
    expect(screen.queryByRole("button", { name: /Pay now/ })).toBeNull();
  });

  it("opens the provider window once for a double click", async () => {
    const user = userEvent.setup();
    let release: (() => void) | undefined;
    vi.mocked(launchCheckout).mockImplementation(
      () => new Promise((resolve) => (release = () => resolve({ wait: "full" }))),
    );
    renderPage();
    const button = await screen.findByRole("button", { name: "Pay now invoice INV-0042" });
    await user.dblClick(button);
    await screen.findByText("Finish paying in the payment window.");
    expect(payment.createCheckout).toHaveBeenCalledTimes(1);
    expect(launchCheckout).toHaveBeenCalledTimes(1);
    release?.();
    expect(await screen.findByText("Payment received.")).toBeTruthy();
  });

  it("tells the customer not to pay again while the server has not confirmed", async () => {
    const user = userEvent.setup();
    vi.mocked(payment.getCheckoutStatus).mockReturnValue(new Promise(() => undefined));
    renderPage();
    await user.click(await screen.findByRole("button", { name: "Pay now invoice INV-0042" }));
    expect(await within(liveRegion()).findByText("Payment pending — don't pay again.")).toBeTruthy();
  });

  it("says the payment failed when the server says so", async () => {
    const user = userEvent.setup();
    vi.mocked(payment.getCheckoutStatus).mockResolvedValue({ ...captured(), state: "failed" });
    renderPage();
    await user.click(await screen.findByRole("button", { name: "Pay now invoice INV-0042" }));
    expect(await screen.findByText("The payment didn't go through.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Pay now invoice INV-0042" })).toBeTruthy();
  });

  it("glances once after a dismissal and offers a status check", async () => {
    const user = userEvent.setup();
    vi.mocked(launchCheckout).mockResolvedValue({ wait: "glance", failed: false });
    vi.mocked(payment.getCheckoutStatus).mockResolvedValue({ ...captured(), state: "created" });
    renderPage();
    await user.click(await screen.findByRole("button", { name: "Pay now invoice INV-0042" }));
    expect(await screen.findByText("The payment window closed before the payment finished.")).toBeTruthy();
    expect(payment.getCheckoutStatus).toHaveBeenCalledTimes(1);

    vi.mocked(payment.getCheckoutStatus).mockResolvedValue(captured());
    await user.click(screen.getByRole("button", { name: "Check status" }));
    expect(await screen.findByText("Payment received.")).toBeTruthy();
  });

  it("explains a blocked payment window", async () => {
    const user = userEvent.setup();
    vi.mocked(launchCheckout).mockRejectedValue(new Error("Razorpay's payment window could not be opened."));
    renderPage();
    await user.click(await screen.findByRole("button", { name: "Pay now invoice INV-0042" }));
    expect(await screen.findByText("Razorpay's payment window could not be opened.")).toBeTruthy();
  });
});

describe("InvoicesPage — refusals", () => {
  it("409: says the invoice is already paid and drops its button", async () => {
    const user = userEvent.setup();
    vi.mocked(payment.createCheckout).mockRejectedValue(new ApiError(409, "Conflict", { status: 409 }));
    renderPage();
    await user.click(await screen.findByRole("button", { name: "Pay now invoice INV-0042" }));
    expect(await screen.findByText("This invoice is already paid.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Pay now/ })).toBeNull();
    expect(launchCheckout).not.toHaveBeenCalled();
  });

  it("404: says the invoice could not be found", async () => {
    const user = userEvent.setup();
    vi.mocked(payment.createCheckout).mockRejectedValue(new ApiError(404, "Not found", { status: 404 }));
    renderPage();
    await user.click(await screen.findByRole("button", { name: "Pay now invoice INV-0042" }));
    expect(await screen.findByText("We couldn't find this invoice.")).toBeTruthy();
    expect(launchCheckout).not.toHaveBeenCalled();
  });

  it("422 currency_unsupported: says why, in this page's words", async () => {
    const user = userEvent.setup();
    vi.mocked(payment.createCheckout).mockRejectedValue(
      new ApiError(422, "Unprocessable", { status: 422, type: "https://errors.opsgrids.dev/currency_unsupported" }),
    );
    renderPage();
    await user.click(await screen.findByRole("button", { name: "Pay now invoice INV-0042" }));
    expect(await screen.findByText("Online payment isn't available for this invoice's currency yet.")).toBeTruthy();
  });

  it("422 phone_required: asks for a number, validates it, and retries with the customer's details", async () => {
    const user = userEvent.setup();
    vi.mocked(payment.createCheckout)
      .mockRejectedValueOnce(
        new ApiError(422, "Unprocessable", {
          status: 422,
          detail: "A phone number is required.",
          errors: [{ field: "customer.phone", code: "phone_required", message: "Required." }],
        }),
      )
      .mockResolvedValueOnce(SESSION);
    renderPage();
    await user.click(await screen.findByRole("button", { name: "Pay now invoice INV-0042" }));

    const dialog = await screen.findByRole("dialog", { name: "Add a mobile number" });
    const field = within(dialog).getByLabelText(/Mobile number/);
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
