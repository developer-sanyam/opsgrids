/**
 * Payment settings for PayU (Hosted Checkout): a merchant key and a salt (as a password) and
 * Sandbox or Production — no webhook secret, because PayU signs its webhooks with the salt — and
 * PayU's own webhook steps once connected.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { MerchantAccountOut } from "@/api/types";
import { renderScreen } from "@/test-utils/renderScreen";
import { PaymentSettingsPage } from "./PaymentSettings";

const getMerchantAccount = vi.fn();
const saveMerchantAccount = vi.fn();

vi.mock("@/api/client", () => ({
  getMerchantAccount: (...a: unknown[]) => getMerchantAccount(...a),
  saveMerchantAccount: (...a: unknown[]) => saveMerchantAccount(...a),
  removeMerchantAccount: vi.fn(),
  getPaymentReconciliation: vi.fn(),
  verifyMerchantAccount: vi.fn(),
}));

const READ = "operations.payments.merchant_account.read:ORG";
const WRITE = "operations.payments.merchant_account.write:ORG";

const NOT_CONNECTED: MerchantAccountOut = {
  configured: false,
  webhook_events: [],
  live_keys_allowed: false,
  wire: "stub",
  wires: { razorpay: "stub", cashfree: "stub", phonepe: "stub", payu: "live" },
};

const PAYU: MerchantAccountOut = {
  configured: true,
  id: "acct-pu",
  provider: "payu",
  key_id: "TESTdevKey01",
  mode: "test",
  key_secret_masked: "••••0001",
  webhook_secret_masked: null,
  webhook_path: "/api/v1/webhooks/collections?account=acct-pu",
  webhook_url: "https://app.opsgrids.example/api/v1/webhooks/collections?account=acct-pu",
  webhook_events: ["Successful", "Failed"],
  connection_state: "unverified",
  live_keys_allowed: false,
  wire: "stub",
  wires: { razorpay: "stub", cashfree: "stub", phonepe: "stub", payu: "live" },
  config: {},
};

describe("PaymentSettingsPage — PayU", () => {
  beforeEach(() => {
    getMerchantAccount.mockReset();
    saveMerchantAccount.mockReset();
  });

  it("connects PayU with a merchant key, a salt and Sandbox — and no webhook secret", async () => {
    const user = userEvent.setup();
    getMerchantAccount.mockResolvedValue(NOT_CONNECTED);
    saveMerchantAccount.mockResolvedValue(PAYU);
    renderScreen(<PaymentSettingsPage />, [READ, WRITE]);

    await user.click(await screen.findByRole("radio", { name: /PayU/ }));
    await user.click(screen.getByRole("button", { name: "Connect PayU" }));
    expect(screen.queryByLabelText(/Webhook/)).toBeNull();
    expect(screen.getByLabelText("Salt").getAttribute("type")).toBe("password");
    await user.type(screen.getByLabelText("Merchant key"), "TESTdevKey01");
    await user.type(screen.getByLabelText("Salt"), "typed-payu-salt");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(saveMerchantAccount).toHaveBeenCalledWith({
        provider: "payu",
        key_id: "TESTdevKey01",
        key_secret: "typed-payu-salt", // pragma: allowlist secret
        mode: "sandbox",
      }),
    );
    await waitFor(() => expect(screen.queryByLabelText("Salt")).toBeNull());
  });

  it("shows a connected account without a webhook secret, and PayU's own steps", async () => {
    getMerchantAccount.mockResolvedValue(PAYU);
    renderScreen(<PaymentSettingsPage />, [READ]);
    expect(await screen.findByText("TESTdevKey01")).toBeDefined();
    expect(screen.getByText("Sandbox")).toBeDefined();
    expect(screen.queryByText("Webhook secret")).toBeNull();
    const panel = screen.getByRole("region", { name: "Webhook" });
    expect(within(panel).getByText(/signs every payment webhook with your salt/)).toBeDefined();
    expect(within(panel).getByText("Successful, Failed")).toBeDefined();
  });
});
