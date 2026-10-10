/**
 * The organization's Razorpay connection screen: secrets are only ever shown masked, typed
 * secrets are sent once and cleared, write actions are hidden without the write grant, the whole
 * screen is refused without the read grant, and the reconciliation report is read-only.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { MerchantAccountOut } from "@/api/types";
import { renderScreen } from "@/test-utils/renderScreen";
import { PaymentSettingsPage } from "./PaymentSettings";

const getMerchantAccount = vi.fn();
const saveMerchantAccount = vi.fn();
const removeMerchantAccount = vi.fn();
const getPaymentReconciliation = vi.fn();

vi.mock("@/api/client", () => ({
  getMerchantAccount: (...a: unknown[]) => getMerchantAccount(...a),
  saveMerchantAccount: (...a: unknown[]) => saveMerchantAccount(...a),
  removeMerchantAccount: (...a: unknown[]) => removeMerchantAccount(...a),
  getPaymentReconciliation: (...a: unknown[]) => getPaymentReconciliation(...a),
}));

const READ = "operations.payments.merchant_account.read:ORG";
const WRITE = "operations.payments.merchant_account.write:ORG";
const RECON = "operations.payments.reconciliation.read:ORG";

const NOT_CONNECTED: MerchantAccountOut = {
  configured: false,
  webhook_events: ["payment.captured"],
  live_keys_allowed: false,
  wire: "razorpay",
};

const CONNECTED: MerchantAccountOut = {
  configured: true,
  id: "acct-1",
  provider: "razorpay",
  key_id: "rzp_test_Merchant0001",
  mode: "test",
  key_secret_masked: "••••0001",
  webhook_secret_masked: "••••0002",
  webhook_path: "/api/v1/webhooks/razorpay/collections?account=acct-1",
  webhook_events: ["payment.captured", "payment.failed"],
  live_keys_allowed: false,
  wire: "razorpay",
};

describe("PaymentSettingsPage", () => {
  beforeEach(() => {
    getMerchantAccount.mockReset();
    saveMerchantAccount.mockReset();
    removeMerchantAccount.mockReset();
    getPaymentReconciliation.mockReset();
  });

  it("refuses the whole screen without the read grant, and never asks the server", async () => {
    renderScreen(<PaymentSettingsPage />, []);
    expect(await screen.findByText(/don't have access/)).toBeDefined();
    expect(getMerchantAccount).not.toHaveBeenCalled();
  });

  it("shows a connected account masked, with the webhook URL to register", async () => {
    getMerchantAccount.mockResolvedValue(CONNECTED);
    renderScreen(<PaymentSettingsPage />, [READ]);
    expect(await screen.findByText("rzp_test_Merchant0001")).toBeDefined();
    expect(screen.getByText("••••0001")).toBeDefined();
    expect(screen.getByText(/\/api\/v1\/webhooks\/razorpay\/collections\?account=acct-1/)).toBeDefined();
    // Read-only: no way to change it.
    expect(screen.queryByRole("button", { name: "Replace credentials" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Disconnect" })).toBeNull();
  });

  it("sends the credentials once and clears them from the form", async () => {
    const user = userEvent.setup();
    getMerchantAccount.mockResolvedValue(NOT_CONNECTED);
    saveMerchantAccount.mockResolvedValue(CONNECTED);
    renderScreen(<PaymentSettingsPage />, [READ, WRITE]);

    await user.click(await screen.findByRole("button", { name: "Connect Razorpay" }));
    await user.type(screen.getByLabelText("Key id"), "rzp_test_Merchant0001");
    await user.type(screen.getByLabelText("Key secret"), "typed-key-secret");
    await user.type(screen.getByLabelText(/Webhook secret/), "typed-hook-secret");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(saveMerchantAccount).toHaveBeenCalledWith({
        provider: "razorpay",
        key_id: "rzp_test_Merchant0001",
        key_secret: "typed-key-secret", // pragma: allowlist secret
        webhook_secret: "typed-hook-secret", // pragma: allowlist secret
      }),
    );
    await waitFor(() => expect(screen.queryByLabelText("Key secret")).toBeNull());
    expect(screen.queryByDisplayValue("typed-key-secret")).toBeNull();
  });

  it("runs the read-only reconciliation and lists only the differences", async () => {
    const user = userEvent.setup();
    getMerchantAccount.mockResolvedValue(CONNECTED);
    getPaymentReconciliation.mockResolvedValue({
      generated_at: "2026-09-29T10:00:00Z",
      checked: 2,
      mismatched: 1,
      unchecked: 0,
      rows: [
        {
          charge_id: "c1",
          reference_type: "operations.order",
          reference_id: "o1",
          state: "created",
          amount_minor: 50000,
          currency: "INR",
          provider_order_id: "order_R9",
          provider_payment_id: null,
          refunded_minor: 0,
          provider_status: "captured",
          mismatches: ["captured_at_provider_only"],
          lookup_failed: false,
        },
        {
          charge_id: "c2",
          reference_type: "operations.order",
          reference_id: "o2",
          state: "captured",
          amount_minor: 50000,
          currency: "INR",
          provider_order_id: "order_R8",
          provider_payment_id: "pay_8",
          refunded_minor: 0,
          provider_status: "captured",
          mismatches: [],
          lookup_failed: false,
        },
      ],
    });
    renderScreen(<PaymentSettingsPage />, [READ, RECON]);

    await user.click(await screen.findByRole("button", { name: "Run check" }));
    expect(await screen.findByText(/Paid at Razorpay, not recorded here/)).toBeDefined();
    expect(screen.getByText("order_R9")).toBeDefined();
    expect(screen.queryByText("order_R8")).toBeNull();
  });
});
