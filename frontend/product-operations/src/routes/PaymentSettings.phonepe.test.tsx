/**
 * Payment settings for PhonePe (Standard Checkout V2): its own fields (Client ID, Client Secret and
 * webhook password as passwords, client version, webhook username, Sandbox or Production), the
 * non-secret config shown back once connected, and PhonePe's own webhook steps.
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
  wires: { razorpay: "stub", cashfree: "stub", phonepe: "live" },
};

const PHONEPE: MerchantAccountOut = {
  configured: true,
  id: "acct-pp",
  provider: "phonepe",
  key_id: "TESTDEVONLYCLIENT01",
  mode: "test",
  key_secret_masked: "••••c0de",
  webhook_secret_masked: "••••word",
  webhook_path: "/api/v1/webhooks/collections?account=acct-pp",
  webhook_url: "https://app.opsgrids.example/api/v1/webhooks/collections?account=acct-pp",
  webhook_events: ["checkout.order.completed", "checkout.order.failed", "pg.refund.completed", "pg.refund.failed"],
  connection_state: "unverified",
  verified_at: null,
  last_error_code: null,
  last_webhook_at: null,
  live_keys_allowed: false,
  wire: "stub",
  wires: { razorpay: "stub", cashfree: "stub", phonepe: "live" },
  config: { client_version: 1, webhook_username: "opsgrids-hook" },
};

describe("PaymentSettingsPage — PhonePe", () => {
  beforeEach(() => {
    getMerchantAccount.mockReset();
    saveMerchantAccount.mockReset();
  });

  it("connects PhonePe with its client credentials, version, webhook pair and Sandbox", async () => {
    const user = userEvent.setup();
    getMerchantAccount.mockResolvedValue(NOT_CONNECTED);
    saveMerchantAccount.mockResolvedValue(PHONEPE);
    renderScreen(<PaymentSettingsPage />, [READ, WRITE]);

    await user.click(await screen.findByRole("radio", { name: /PhonePe/ }));
    await user.click(screen.getByRole("button", { name: "Connect PhonePe" }));
    for (const secret of ["Client Secret", "Webhook password"]) {
      expect(screen.getByLabelText(secret).getAttribute("type")).toBe("password");
    }
    expect((screen.getByRole("radio", { name: /Production/ }) as HTMLInputElement).disabled).toBe(true);
    await user.type(screen.getByLabelText("Client ID"), "TESTDEVONLYCLIENT01");
    await user.type(screen.getByLabelText("Client Secret"), "typed-client-secret");
    await user.type(screen.getByLabelText(/Client version/), "1");
    await user.type(screen.getByLabelText(/Webhook username/), "opsgrids-hook");
    await user.type(screen.getByLabelText("Webhook password"), "typed-hook-password");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(saveMerchantAccount).toHaveBeenCalledWith({
        provider: "phonepe",
        key_id: "TESTDEVONLYCLIENT01",
        key_secret: "typed-client-secret", // pragma: allowlist secret
        webhook_secret: "typed-hook-password", // pragma: allowlist secret
        mode: "sandbox",
        config: { client_version: 1, webhook_username: "opsgrids-hook" },
      }),
    );
    await waitFor(() => expect(screen.queryByLabelText("Client Secret")).toBeNull());
    expect(screen.queryByDisplayValue("typed-client-secret")).toBeNull();
    expect(screen.queryByDisplayValue("typed-hook-password")).toBeNull();
  });

  it("shows a connected account's config and masks, and PhonePe's own webhook steps", async () => {
    getMerchantAccount.mockResolvedValue(PHONEPE);
    renderScreen(<PaymentSettingsPage />, [READ]);
    expect(await screen.findByText("TESTDEVONLYCLIENT01")).toBeDefined();
    expect(screen.getByText("Sandbox")).toBeDefined();
    expect(screen.getByText("opsgrids-hook")).toBeDefined();
    expect(screen.getByText("••••word")).toBeDefined();
    expect((screen.getByRole("radio", { name: /PhonePe/ }) as HTMLInputElement).checked).toBe(true);
    const panel = screen.getByRole("region", { name: "Webhook" });
    expect(within(panel).getByText(PHONEPE.webhook_url ?? "")).toBeDefined();
    expect(within(panel).getByText(/username and password/)).toBeDefined();
    expect(within(panel).getByText(/checkout.order.completed/)).toBeDefined();
  });

  it("replacing credentials keeps the stored version and username in the form", async () => {
    const user = userEvent.setup();
    getMerchantAccount.mockResolvedValue(PHONEPE);
    renderScreen(<PaymentSettingsPage />, [READ, WRITE]);
    await user.click(await screen.findByRole("button", { name: "Replace credentials" }));
    expect((screen.getByLabelText(/Client version/) as HTMLInputElement).value).toBe("1");
    expect((screen.getByLabelText(/Webhook username/) as HTMLInputElement).value).toBe("opsgrids-hook");
    expect((screen.getByLabelText("Client Secret") as HTMLInputElement).value).toBe("");
  });
});
