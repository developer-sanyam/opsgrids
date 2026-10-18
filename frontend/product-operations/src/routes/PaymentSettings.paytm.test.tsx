/**
 * Payment settings for Paytm: a MID and a merchant key (as a password) and Sandbox or Production —
 * no webhook secret, because Paytm signs its webhooks with the merchant key — and Paytm's own
 * webhook steps once connected.
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

const WIRES = { razorpay: "stub", cashfree: "stub", phonepe: "stub", payu: "stub", paytm: "live" } as const;

const PAYTM: MerchantAccountOut = {
  configured: true,
  id: "acct-pt",
  provider: "paytm",
  key_id: "TESTMID0001",
  mode: "test",
  key_secret_masked: "••••-016",
  webhook_secret_masked: null,
  webhook_path: "/api/v1/webhooks/collections?account=acct-pt",
  webhook_url: "https://app.opsgrids.example/api/v1/webhooks/collections?account=acct-pt",
  webhook_events: ["Payment Status"],
  connection_state: "unverified",
  live_keys_allowed: false,
  wire: "stub",
  wires: WIRES,
  config: {},
};

describe("PaymentSettingsPage — Paytm", () => {
  beforeEach(() => {
    getMerchantAccount.mockReset();
    saveMerchantAccount.mockReset();
  });

  it("connects Paytm with a MID, a merchant key and Sandbox — and no webhook secret", async () => {
    const user = userEvent.setup();
    getMerchantAccount.mockResolvedValue({
      configured: false,
      webhook_events: [],
      live_keys_allowed: false,
      wire: "stub",
      wires: WIRES,
    });
    saveMerchantAccount.mockResolvedValue(PAYTM);
    renderScreen(<PaymentSettingsPage />, [READ, WRITE]);

    await user.click(await screen.findByRole("radio", { name: /Paytm/ }));
    await user.click(screen.getByRole("button", { name: "Connect Paytm" }));
    expect(screen.queryByLabelText(/Webhook/)).toBeNull();
    expect(screen.getByLabelText("Merchant key").getAttribute("type")).toBe("password");
    await user.type(screen.getByLabelText("MID"), "TESTMID0001");
    await user.type(screen.getByLabelText("Merchant key"), "typed-key-000016");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(saveMerchantAccount).toHaveBeenCalledWith({
        provider: "paytm",
        key_id: "TESTMID0001",
        key_secret: "typed-key-000016", // pragma: allowlist secret
        mode: "sandbox",
      }),
    );
  });

  it("shows a connected account without a webhook secret, and Paytm's own steps", async () => {
    getMerchantAccount.mockResolvedValue(PAYTM);
    renderScreen(<PaymentSettingsPage />, [READ]);
    expect(await screen.findByText("TESTMID0001")).toBeDefined();
    expect(screen.queryByText("Webhook secret")).toBeNull();
    const panel = screen.getByRole("region", { name: "Webhook" });
    expect(within(panel).getByText(/Payment Status webhook/)).toBeDefined();
    expect(within(panel).getByText(/signs every payment status with your merchant key/)).toBeDefined();
  });
});
