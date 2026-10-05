/**
 * Payment settings with a choice of provider (ADR-0030): Cashfree's own fields (App ID, Secret Key
 * as a password, Sandbox or Production — no webhook secret), the provider fixed once connected,
 * a connection check with a state badge, the webhook panel with a copyable URL and Cashfree's own
 * steps, and a plain sentence when the session did not sign in with a one-time code.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { MerchantAccountOut } from "@/api/types";
import { renderScreen } from "@/test-utils/renderScreen";
import { ApiError } from "@opsgrids/client";
import { PaymentSettingsPage } from "./PaymentSettings";

const getMerchantAccount = vi.fn();
const saveMerchantAccount = vi.fn();
const removeMerchantAccount = vi.fn();
const getPaymentReconciliation = vi.fn();
const verifyMerchantAccount = vi.fn();

vi.mock("@/api/client", () => ({
  getMerchantAccount: (...a: unknown[]) => getMerchantAccount(...a),
  saveMerchantAccount: (...a: unknown[]) => saveMerchantAccount(...a),
  removeMerchantAccount: (...a: unknown[]) => removeMerchantAccount(...a),
  getPaymentReconciliation: (...a: unknown[]) => getPaymentReconciliation(...a),
  verifyMerchantAccount: (...a: unknown[]) => verifyMerchantAccount(...a),
}));

const READ = "operations.payments.merchant_account.read:ORG";
const WRITE = "operations.payments.merchant_account.write:ORG";
const RECON = "operations.payments.reconciliation.read:ORG";

const NOT_CONNECTED: MerchantAccountOut = {
  configured: false,
  webhook_events: ["payment.captured"],
  live_keys_allowed: false,
  wire: "stub",
  wires: { razorpay: "stub", cashfree: "live" },
};

const CASHFREE: MerchantAccountOut = {
  configured: true,
  id: "acct-cf",
  provider: "cashfree",
  key_id: "TESTdevonlyapp01",
  mode: "test",
  key_secret_masked: "••••9f3a",
  webhook_secret_masked: null,
  webhook_path: "/api/v1/webhooks/collections?account=acct-cf",
  webhook_url: "https://app.opsgrids.example/api/v1/webhooks/collections?account=acct-cf",
  webhook_events: ["PAYMENT_SUCCESS_WEBHOOK", "PAYMENT_FAILED_WEBHOOK", "PAYMENT_USER_DROPPED_WEBHOOK", "REFUND_STATUS_WEBHOOK"],
  connection_state: "unverified",
  verified_at: null,
  last_error_code: null,
  last_webhook_at: null,
  live_keys_allowed: false,
  wire: "stub",
  wires: { razorpay: "stub", cashfree: "live" },
};

function otpRefusal(): ApiError {
  return new ApiError(403, "Additional authentication required: otp", {
    type: "https://errors.opsgrids.dev/forbidden",
    title: "Not permitted",
    status: 403,
    detail: "Additional authentication required: otp",
  });
}

describe("PaymentSettingsPage — Cashfree", () => {
  beforeEach(() => {
    for (const fn of [getMerchantAccount, saveMerchantAccount, removeMerchantAccount, getPaymentReconciliation, verifyMerchantAccount]) {
      fn.mockReset();
    }
  });

  it("connects Cashfree with an App ID, a Secret Key and Sandbox — and no webhook secret", async () => {
    const user = userEvent.setup();
    getMerchantAccount.mockResolvedValue(NOT_CONNECTED);
    saveMerchantAccount.mockResolvedValue(CASHFREE);
    renderScreen(<PaymentSettingsPage />, [READ, WRITE]);

    await user.click(await screen.findByRole("radio", { name: /Cashfree/ }));
    await user.click(screen.getByRole("button", { name: "Connect Cashfree" }));
    expect(screen.queryByLabelText(/Webhook secret/)).toBeNull();
    const secret = screen.getByLabelText("Secret Key");
    expect(secret.getAttribute("type")).toBe("password");
    // Production is not enabled here: it cannot even be chosen.
    expect((screen.getByRole("radio", { name: /Production/ }) as HTMLInputElement).disabled).toBe(true);
    await user.type(screen.getByLabelText("App ID"), "TESTdevonlyapp01");
    await user.type(secret, "typed-cashfree-secret");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(saveMerchantAccount).toHaveBeenCalledWith({
        provider: "cashfree",
        key_id: "TESTdevonlyapp01",
        key_secret: "typed-cashfree-secret", // pragma: allowlist secret
        mode: "sandbox",
      }),
    );
    await waitFor(() => expect(screen.queryByLabelText("Secret Key")).toBeNull());
    expect(screen.queryByDisplayValue("typed-cashfree-secret")).toBeNull();
  });

  it("shows a connected Cashfree account read-only by provider, with its environment and state", async () => {
    getMerchantAccount.mockResolvedValue(CASHFREE);
    renderScreen(<PaymentSettingsPage />, [READ]);
    expect(await screen.findByText("TESTdevonlyapp01")).toBeDefined();
    expect(screen.getByText("Sandbox")).toBeDefined();
    expect(screen.getByText("••••9f3a")).toBeDefined();
    expect(screen.queryByText("Webhook secret")).toBeNull();
    expect(screen.getByText("Not checked")).toBeDefined();
    for (const radio of screen.getAllByRole("radio")) {
      expect((radio as HTMLInputElement).disabled).toBe(true);
    }
    expect((screen.getByRole("radio", { name: /Cashfree/ }) as HTMLInputElement).checked).toBe(true);
    // Without the write grant there is nothing to press.
    expect(screen.queryByRole("button", { name: "Check connection" })).toBeNull();
  });

  it("gives the webhook URL to copy, Cashfree's own steps, and says none has arrived yet", async () => {
    getMerchantAccount.mockResolvedValue(CASHFREE);
    renderScreen(<PaymentSettingsPage />, [READ]);
    const panel = await screen.findByRole("region", { name: "Webhook" });
    expect(within(panel).getByText(CASHFREE.webhook_url ?? "")).toBeDefined();
    expect(within(panel).getByRole("button", { name: "Copy Webhook URL" })).toBeDefined();
    expect(within(panel).getByText(/2025-01-01 or later/)).toBeDefined();
    expect(within(panel).getByText(/Whitelisting/)).toBeDefined();
    expect(within(panel).getByText(/no webhook secret/i)).toBeDefined();
    expect(within(panel).getByText(/None yet/)).toBeDefined();
  });

  it("checks the connection with the server and shows what it now says", async () => {
    const user = userEvent.setup();
    getMerchantAccount.mockResolvedValueOnce(CASHFREE).mockResolvedValue({
      ...CASHFREE,
      connection_state: "verified",
      verified_at: "2026-10-06T10:00:00Z",
    });
    verifyMerchantAccount.mockResolvedValue({ ...CASHFREE, connection_state: "verified" });
    renderScreen(<PaymentSettingsPage />, [READ, WRITE]);

    await user.click(await screen.findByRole("button", { name: "Check connection" }));
    await waitFor(() => expect(verifyMerchantAccount).toHaveBeenCalledTimes(1));
    expect(await screen.findByText("Verified")).toBeDefined();
  });

  it("explains a failed check in Cashfree's terms", async () => {
    getMerchantAccount.mockResolvedValue({ ...CASHFREE, connection_state: "failed", last_error_code: "credentials_rejected" });
    renderScreen(<PaymentSettingsPage />, [READ]);
    expect(await screen.findByText("Check failed")).toBeDefined();
    expect(screen.getByText(/Sandbox or Production/)).toBeDefined();
  });

  it("turns a session without a one-time code into a sentence that says how to fix it", async () => {
    const user = userEvent.setup();
    getMerchantAccount.mockResolvedValue(CASHFREE);
    verifyMerchantAccount.mockRejectedValue(otpRefusal());
    removeMerchantAccount.mockRejectedValue(otpRefusal());
    renderScreen(<PaymentSettingsPage />, [READ, WRITE]);

    await user.click(await screen.findByRole("button", { name: "Check connection" }));
    expect(await screen.findByText(/sign in again with your authenticator code/)).toBeDefined();

    await user.click(screen.getByRole("button", { name: "Disconnect" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Disconnect" }));
    expect(await within(dialog).findByText(/needs a session signed in with a one-time code/)).toBeDefined();
  });

  it("shows the server's count when open payments block a disconnect", async () => {
    const user = userEvent.setup();
    getMerchantAccount.mockResolvedValue(CASHFREE);
    removeMerchantAccount.mockRejectedValue(
      new ApiError(409, "blocked", {
        type: "https://errors.opsgrids.dev/disconnect-blocked",
        title: "Online payments are still open on this account",
        status: 409,
        detail: "2 online payments are still open on this account. Cancel them from their orders, or wait for them to be paid or to expire, then disconnect.",
      }),
    );
    renderScreen(<PaymentSettingsPage />, [READ, WRITE]);
    await user.click(await screen.findByRole("button", { name: "Disconnect" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Disconnect" }));
    expect(await within(dialog).findByText(/2 online payments are still open/)).toBeDefined();
  });

  it("names Cashfree in the reconciliation", async () => {
    const user = userEvent.setup();
    getMerchantAccount.mockResolvedValue(CASHFREE);
    getPaymentReconciliation.mockResolvedValue({
      generated_at: "2026-10-06T10:00:00Z",
      checked: 1,
      mismatched: 1,
      unchecked: 0,
      rows: [
        {
          charge_id: "c1",
          reference_type: "operations.order",
          reference_id: "o1",
          state: "captured",
          amount_minor: 50000,
          currency: "INR",
          provider_order_id: "c1",
          provider_payment_id: "3001",
          refunded_minor: 0,
          provider_status: null,
          mismatches: ["not_captured_at_provider"],
          lookup_failed: false,
        },
      ],
    });
    renderScreen(<PaymentSettingsPage />, [READ, RECON]);
    await user.click(await screen.findByRole("button", { name: "Run check" }));
    expect(await screen.findByText(/Recorded as paid here, not paid at Cashfree/)).toBeDefined();
  });
});
