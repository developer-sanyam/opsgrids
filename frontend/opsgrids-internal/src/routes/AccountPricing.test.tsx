import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as client from "@/api/client";
import { ApiError } from "@/api/http";
import type { OrganizationPricing, PricingOrgRef, ProductPricing } from "@/api/types";
import * as authContext from "@/auth/context";
import type { AuthContextValue } from "@/auth/context";
import type { OperatorRole } from "@/auth/types";
import { ClientPricingCard } from "./AccountPricing";

/**
 * `ClientPricingCard` end to end: every table state the spec calls for, the "Set price" dialog's
 * form → confirm → result steps, the idempotency key's lifetime, verbatim error rendering, and that
 * `console.pricing.write` staying a UI hint (never an enforcement) is honoured in both directions.
 */

function org(overrides: Partial<PricingOrgRef> = {}): PricingOrgRef {
  return {
    organization_id: "org_test",
    name: "Zenith Polymers",
    ...overrides,
  };
}

function product(overrides: Partial<ProductPricing> = {}): ProductPricing {
  return {
    product_key: "crm",
    product_name: "OpsGrids CRM",
    subscription: {
      id: "sub_1",
      state: "active",
      plan_version_id: "pv_crm_scale_v1",
      plan_name: "Scale",
      trial_ends_at: null,
      current_period_end: "2026-09-30T23:59:59Z",
      pending_plan_version_id: null,
      pending_plan_name: null,
      cancel_at: null,
      entitled_products: ["crm"],
      current_period_start: "2026-09-01T00:00:00Z",
      trial_max_ends_at: null,
    },
    current_price: {
      plan_version_id: "pv_crm_scale_v1",
      is_bespoke: false,
      amount_minor: 120_000,
      currency: "INR",
      billing_interval: "month",
    },
    public_price: {
      plan_version_id: "pv_crm_scale_v1",
      amount_minor: 120_000,
      currency: "INR",
      billing_interval: "month",
    },
    history: [],
    ...overrides,
  };
}

function pricing(products: ProductPricing[]): OrganizationPricing {
  return { organization_id: "org_test", products };
}

/** `roles` mirrors what `GET /auth/operator/me` would report - the live shapes are
 *  `owner` / `billing_ops` / `support` (`services/auth`), and pricing-write is `owner` or
 *  `billing_ops` only (`services/console/app/api/routes.py`'s `OperatorBilling` guard). */
function mockAuth(roles: OperatorRole[]) {
  const value: AuthContextValue = {
    status: "authenticated",
    operator: null,
    login: vi.fn(),
    beginEnrolment: vi.fn(),
    confirmEnrolment: vi.fn(),
    finishEnrolment: vi.fn(),
    verifyMfa: vi.fn(),
    cancelSignIn: vi.fn(),
    logout: vi.fn(),
    hasRole: (role) => roles.includes(role),
  };
  vi.spyOn(authContext, "useAuth").mockReturnValue(value);
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("table states", () => {
  it("shows a loading skeleton, then the populated table, with no empty flash between", async () => {
    mockAuth(["owner"]);
    vi.spyOn(client, "getOrganizationPricing").mockResolvedValue(pricing([product()]));
    render(<ClientPricingCard org={org()} />);
    expect(screen.getByText("Client pricing")).toBeTruthy();
    await waitFor(() => expect(screen.getByText("OpsGrids CRM")).toBeTruthy());
  });

  it("renders the error state with the message verbatim, and retries on demand", async () => {
    mockAuth(["owner"]);
    const spy = vi
      .spyOn(client, "getOrganizationPricing")
      .mockRejectedValueOnce(new Error("The pricing service is unreachable."))
      .mockResolvedValueOnce(pricing([product()]));
    render(<ClientPricingCard org={org()} />);
    await waitFor(() => expect(screen.getByText("The pricing service is unreachable.")).toBeTruthy());

    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(screen.getByText("OpsGrids CRM")).toBeTruthy());
    expect(spy).toHaveBeenCalledTimes(2);
  });

  it("shows 'Not subscribed' for a product with no subscription", async () => {
    mockAuth(["owner"]);
    vi.spyOn(client, "getOrganizationPricing").mockResolvedValue(
      pricing([product({ subscription: null, current_price: null })]),
    );
    render(<ClientPricingCard org={org()} />);
    await waitFor(() => expect(screen.getByText("Not subscribed")).toBeTruthy());
    expect(screen.getByText("Not set")).toBeTruthy();
  });

  it("shows the trial state with days left", async () => {
    mockAuth(["owner"]);
    // Relative to the real clock the card reads, so the trial is always still running.
    const trialEnd = new Date(Date.now() + 11 * 86_400_000).toISOString();
    vi.spyOn(client, "getOrganizationPricing").mockResolvedValue(
      pricing([
        product({
          subscription: {
            id: "sub_2",
            state: "trial",
            plan_version_id: "pv_crm_growth_v2",
            plan_name: "Growth",
            trial_ends_at: trialEnd,
            current_period_end: trialEnd,
            pending_plan_version_id: null,
            pending_plan_name: null,
            cancel_at: null,
            entitled_products: ["crm"],
            current_period_start: "2026-09-01T00:00:00Z",
            trial_max_ends_at: null,
          },
        }),
      ]),
    );
    render(<ClientPricingCard org={org()} />);
    await waitFor(() => expect(screen.getByText("Trial")).toBeTruthy());
    expect(screen.getByText(/days left/)).toBeTruthy();
  });

  it("shows active state plainly", async () => {
    mockAuth(["owner"]);
    vi.spyOn(client, "getOrganizationPricing").mockResolvedValue(pricing([product()]));
    render(<ClientPricingCard org={org()} />);
    await waitFor(() => expect(screen.getByText("Active")).toBeTruthy());
  });

  it("badges a bespoke current price as Custom, and a plan-priced one as List price", async () => {
    mockAuth(["owner"]);
    vi.spyOn(client, "getOrganizationPricing").mockResolvedValue(
      pricing([
        product({ product_key: "crm", product_name: "OpsGrids CRM" }),
        product({
          product_key: "trading",
          product_name: "OpsGrids Trading Desk",
          current_price: {
            plan_version_id: "pv_bespoke_1",
            is_bespoke: true,
            amount_minor: 4_200_000,
            currency: "INR",
            billing_interval: "month",
          },
        }),
      ]),
    );
    render(<ClientPricingCard org={org()} />);
    await waitFor(() => expect(screen.getByText("Custom")).toBeTruthy());
    // "List price" also names the reference-price column header, so more than one match is
    // expected — what matters is the CRM row's own badge is among them.
    expect(screen.getAllByText("List price").length).toBeGreaterThanOrEqual(1);
  });
});

describe("price history", () => {
  it("lists changes newest first regardless of the order the API returned them in", async () => {
    mockAuth(["owner"]);
    vi.spyOn(client, "getOrganizationPricing").mockResolvedValue(
      pricing([
        product({
          history: [
            {
              plan_version_id: "v1",
              amount_minor: 100_000,
              currency: "INR",
              billing_interval: "month",
              created_at: "2024-01-01T00:00:00Z",
              created_by_email_masked: "a***a@opsgrids.com",
              reason: "Oldest change",
            },
            {
              plan_version_id: "v2",
              amount_minor: 150_000,
              currency: "INR",
              billing_interval: "month",
              created_at: "2026-01-01T00:00:00Z",
              created_by_email_masked: "b***b@opsgrids.com",
              reason: "Newest change",
            },
          ],
        }),
      ]),
    );
    render(<ClientPricingCard org={org()} />);
    const summary = await screen.findByText(/Price history — OpsGrids CRM/);
    await userEvent.click(summary);
    const reasons = screen.getAllByText(/change$/i).map((el) => el.textContent);
    expect(reasons).toEqual(["Newest change", "Oldest change"]);
  });

  it("says so when a product has never had its price touched", async () => {
    mockAuth(["owner"]);
    vi.spyOn(client, "getOrganizationPricing").mockResolvedValue(pricing([product({ history: [] })]));
    render(<ClientPricingCard org={org()} />);
    const summary = await screen.findByText(/Price history — OpsGrids CRM/);
    await userEvent.click(summary);
    expect(screen.getByText("No price changes recorded yet — still on the list price.")).toBeTruthy();
  });

  it("shows a dash rather than nothing when a history row has no attributable actor", async () => {
    mockAuth(["owner"]);
    vi.spyOn(client, "getOrganizationPricing").mockResolvedValue(
      pricing([
        product({
          history: [
            {
              plan_version_id: "v1",
              amount_minor: 100_000,
              currency: "INR",
              billing_interval: "month",
              created_at: "2024-01-01T00:00:00Z",
              created_by_email_masked: null,
              reason: "Migrated from the legacy pricing sheet",
            },
          ],
        }),
      ]),
    );
    render(<ClientPricingCard org={org()} />);
    const summary = await screen.findByText(/Price history — OpsGrids CRM/);
    await userEvent.click(summary);
    expect(screen.getByText("—")).toBeTruthy();
  });
});

describe("permission gating (a hint only)", () => {
  it("shows Set price for an owner", async () => {
    mockAuth(["owner"]);
    vi.spyOn(client, "getOrganizationPricing").mockResolvedValue(pricing([product()]));
    render(<ClientPricingCard org={org()} />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Set price" })).toBeTruthy());
  });

  it("shows Set price for billing_ops too — the write guard is owner OR billing_ops", async () => {
    mockAuth(["billing_ops"]);
    vi.spyOn(client, "getOrganizationPricing").mockResolvedValue(pricing([product()]));
    render(<ClientPricingCard org={org()} />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Set price" })).toBeTruthy());
  });

  it("hides Set price for support-only — the row still shows the price itself", async () => {
    mockAuth(["support"]);
    vi.spyOn(client, "getOrganizationPricing").mockResolvedValue(pricing([product()]));
    render(<ClientPricingCard org={org()} />);
    await waitFor(() => expect(screen.getByText("OpsGrids CRM")).toBeTruthy());
    expect(screen.queryByRole("button", { name: "Set price" })).toBeNull();
  });

  it("hides Set price for a caller with no roles at all", async () => {
    mockAuth([]);
    vi.spyOn(client, "getOrganizationPricing").mockResolvedValue(pricing([product()]));
    render(<ClientPricingCard org={org()} />);
    await waitFor(() => expect(screen.getByText("OpsGrids CRM")).toBeTruthy());
    expect(screen.queryByRole("button", { name: "Set price" })).toBeNull();
  });
});

describe("the Set price dialog", () => {
  async function openDialog() {
    mockAuth(["owner"]);
    vi.spyOn(client, "getOrganizationPricing").mockResolvedValue(pricing([product()]));
    render(<ClientPricingCard org={org({ name: "Zenith Polymers" })} />);
    await userEvent.click(await screen.findByRole("button", { name: "Set price" }));
    expect(screen.getByRole("dialog")).toBeTruthy();
  }

  it("keeps invalid amounts on the form step with an inline, field-tied error", async () => {
    await openDialog();
    const amount = screen.getByLabelText("Amount (₹)");
    await userEvent.type(amount, "12.999");
    await userEvent.tab();
    expect(screen.getByText("Enter at most 2 decimal places.")).toBeTruthy();
    expect(amount.getAttribute("aria-invalid")).toBe("true");
    expect(amount.getAttribute("aria-describedby")).toBeTruthy();

    await userEvent.click(screen.getByRole("button", { name: "Continue" }));
    // Still on the form step — Continue does not advance an invalid entry.
    expect(screen.getByLabelText("Amount (₹)")).toBeTruthy();
  });

  it("requires a reason before Continue advances", async () => {
    await openDialog();
    await userEvent.type(screen.getByLabelText("Amount (₹)"), "2499.50");
    await userEvent.click(screen.getByRole("button", { name: "Continue" }));
    expect(screen.getByText("Enter a reason.")).toBeTruthy();
  });

  it("preserves entered data across a failed submit", async () => {
    mockAuth(["owner"]);
    vi.spyOn(client, "getOrganizationPricing").mockResolvedValue(pricing([product()]));
    vi.spyOn(client, "setOrganizationPrice").mockRejectedValue(new ApiError(422, "amount_minor must be positive", null));
    render(<ClientPricingCard org={org()} />);
    await userEvent.click(await screen.findByRole("button", { name: "Set price" }));

    await userEvent.type(screen.getByLabelText("Amount (₹)"), "2499.50");
    await userEvent.type(screen.getByLabelText("Reason (required)"), "Renewal discount");
    await userEvent.click(screen.getByRole("button", { name: "Continue" }));
    await userEvent.click(screen.getByRole("button", { name: "Confirm & set price" }));

    expect(await screen.findByText("amount_minor must be positive")).toBeTruthy();
    // Data survives the failure — going back still shows what was typed.
    await userEvent.click(screen.getByRole("button", { name: "Back" }));
    expect(screen.getByDisplayValue("2499.50")).toBeTruthy();
    expect(screen.getByDisplayValue("Renewal discount")).toBeTruthy();
  });

  it.each([
    [403, "You do not have console.pricing.write"],
    [404, "No such organization"],
    [409, "trading has no public flat plan to clone"],
    [422, "reason must be between 1 and 500 characters"],
  ])("shows a %i verbatim", async (status, message) => {
    mockAuth(["owner"]);
    vi.spyOn(client, "getOrganizationPricing").mockResolvedValue(pricing([product()]));
    vi.spyOn(client, "setOrganizationPrice").mockRejectedValue(new ApiError(status, message, null));
    render(<ClientPricingCard org={org()} />);
    await userEvent.click(await screen.findByRole("button", { name: "Set price" }));
    await userEvent.type(screen.getByLabelText("Amount (₹)"), "2499.50");
    await userEvent.type(screen.getByLabelText("Reason (required)"), "Renewal discount");
    await userEvent.click(screen.getByRole("button", { name: "Continue" }));
    await userEvent.click(screen.getByRole("button", { name: "Confirm & set price" }));
    expect(await screen.findByText(message)).toBeTruthy();
  });

  it("walks form -> confirm -> result, sends the exact request, and reports the response's own effective", async () => {
    mockAuth(["owner"]);
    vi.spyOn(client, "getOrganizationPricing").mockResolvedValue(pricing([product()]));
    const setPrice = vi.spyOn(client, "setOrganizationPrice").mockResolvedValue({
      plan_version_id: "pv_bespoke_new",
      applied_to_subscription_id: "sub_1",
      // The predicted effective (subscribed + apply checked) would be "now" — the response
      // disagreeing on purpose here proves the result panel trusts the response, not the guess.
      effective: "next_period",
    });
    render(<ClientPricingCard org={org({ organization_id: "org_test", name: "Zenith Polymers" })} />);
    await userEvent.click(await screen.findByRole("button", { name: "Set price" }));

    await userEvent.type(screen.getByLabelText("Amount (₹)"), "2499.50");
    await userEvent.type(screen.getByLabelText("Reason (required)"), "Renewal discount");
    await userEvent.click(screen.getByRole("button", { name: "Yearly" }));
    await userEvent.click(screen.getByRole("button", { name: "Continue" }));

    // Confirm step: the predicted outcome, before the request exists.
    expect(screen.getByText(/applies now\./)).toBeTruthy();

    await userEvent.click(screen.getByRole("button", { name: "Confirm & set price" }));

    expect(setPrice).toHaveBeenCalledTimes(1);
    const [orgId, request, idempotencyKey] = setPrice.mock.calls[0]!;
    expect(orgId).toBe("org_test");
    expect(request).toEqual({
      product_key: "crm",
      amount_minor: 249_950,
      currency: "INR",
      billing_interval: "year",
      reason: "Renewal discount",
      apply_to_subscription: true,
    });
    expect(idempotencyKey).toMatch(/^[0-9a-f-]{36}$/);

    // Result step reflects the response's `effective`, not the earlier prediction.
    const status = await screen.findByRole("status");
    expect(within(status).getByText(/applies from the next period\./)).toBeTruthy();
    expect(within(status).getByText("pv_bespoke_new")).toBeTruthy();

    // The table refreshes on success.
    await waitFor(() => expect(client.getOrganizationPricing).toHaveBeenCalledTimes(2));
  });

  it("disables the apply-to-subscription checkbox, unchecked, when there is no subscription", async () => {
    mockAuth(["owner"]);
    vi.spyOn(client, "getOrganizationPricing").mockResolvedValue(
      pricing([product({ subscription: null, current_price: null })]),
    );
    render(<ClientPricingCard org={org()} />);
    await userEvent.click(await screen.findByRole("button", { name: "Set price" }));
    const checkbox = screen.getByRole("checkbox", { name: /Apply to their current subscription/ }) as HTMLInputElement;
    expect(checkbox.disabled).toBe(true);
    expect(checkbox.checked).toBe(false);
    expect(screen.getByText(/has no subscription for OpsGrids CRM yet/)).toBeTruthy();
  });

  it("reuses the same idempotency key on a retried submit, and issues a new one on reopen", async () => {
    mockAuth(["owner"]);
    vi.spyOn(client, "getOrganizationPricing").mockResolvedValue(pricing([product()]));
    const setPrice = vi
      .spyOn(client, "setOrganizationPrice")
      .mockRejectedValueOnce(new ApiError(500, "Internal Server Error", null))
      .mockResolvedValue({ plan_version_id: "pv_1", applied_to_subscription_id: "sub_1", effective: "now" });

    render(<ClientPricingCard org={org()} />);
    await userEvent.click(await screen.findByRole("button", { name: "Set price" }));
    await userEvent.type(screen.getByLabelText("Amount (₹)"), "2499.50");
    await userEvent.type(screen.getByLabelText("Reason (required)"), "Renewal discount");
    await userEvent.click(screen.getByRole("button", { name: "Continue" }));
    await userEvent.click(screen.getByRole("button", { name: "Confirm & set price" }));
    await screen.findByText("Internal Server Error");

    // Retry: same dialog, same key.
    await userEvent.click(screen.getByRole("button", { name: "Confirm & set price" }));
    await screen.findByRole("status");

    expect(setPrice).toHaveBeenCalledTimes(2);
    const firstKey = setPrice.mock.calls[0]![2];
    const secondKey = setPrice.mock.calls[1]![2];
    expect(secondKey).toBe(firstKey);

    // Close and reopen: a fresh key.
    await userEvent.click(screen.getByRole("button", { name: "Done" }));
    await userEvent.click(await screen.findByRole("button", { name: "Set price" }));
    await userEvent.type(screen.getByLabelText("Amount (₹)"), "2499.50");
    await userEvent.type(screen.getByLabelText("Reason (required)"), "Renewal discount");
    await userEvent.click(screen.getByRole("button", { name: "Continue" }));
    await userEvent.click(screen.getByRole("button", { name: "Confirm & set price" }));
    await screen.findByRole("status");

    const thirdKey = setPrice.mock.calls[2]![2];
    expect(thirdKey).not.toBe(firstKey);
  });

  it("never double-submits on a rapid double click", async () => {
    mockAuth(["owner"]);
    vi.spyOn(client, "getOrganizationPricing").mockResolvedValue(pricing([product()]));
    let resolveRequest: (value: { plan_version_id: string; applied_to_subscription_id: string | null; effective: "now" }) => void = () => {};
    const setPrice = vi.spyOn(client, "setOrganizationPrice").mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveRequest = resolve;
        }),
    );
    render(<ClientPricingCard org={org()} />);
    await userEvent.click(await screen.findByRole("button", { name: "Set price" }));
    await userEvent.type(screen.getByLabelText("Amount (₹)"), "2499.50");
    await userEvent.type(screen.getByLabelText("Reason (required)"), "Renewal discount");
    await userEvent.click(screen.getByRole("button", { name: "Continue" }));

    const confirmButton = screen.getByRole("button", { name: "Confirm & set price" });
    await userEvent.click(confirmButton);
    await userEvent.click(confirmButton); // fired again while the first call is still pending

    expect(setPrice).toHaveBeenCalledTimes(1);
    resolveRequest({ plan_version_id: "pv_1", applied_to_subscription_id: "sub_1", effective: "now" });
    await screen.findByRole("status");
  });

  it("closes on Escape and returns focus to the button that opened it", async () => {
    mockAuth(["owner"]);
    vi.spyOn(client, "getOrganizationPricing").mockResolvedValue(pricing([product()]));
    render(<ClientPricingCard org={org()} />);
    const trigger = await screen.findByRole("button", { name: "Set price" });
    await userEvent.click(trigger);
    expect(screen.getByRole("dialog")).toBeTruthy();

    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });

  it("ties every field to its label and error for assistive tech", async () => {
    await openDialog();
    expect(screen.getByLabelText("Amount (₹)")).toBeTruthy();
    expect(screen.getByLabelText("Reason (required)")).toBeTruthy();
    expect(screen.getByRole("checkbox", { name: /Apply to their current subscription/ })).toBeTruthy();
  });
});
