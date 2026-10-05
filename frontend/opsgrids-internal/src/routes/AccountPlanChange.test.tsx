import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { MockInstance } from "vitest";

import * as client from "@/api/client";
import { ApiError } from "@/api/http";
import type {
  OrganizationPricing,
  PlanChangePreview,
  PlanChangeRequest,
  PlanChangeResult,
  PlanOption,
  PlanOptions,
  PricingOrgRef,
  ProductPricing,
} from "@/api/types";
import * as authContext from "@/auth/context";
import type { AuthContextValue } from "@/auth/context";
import type { OperatorRole } from "@/auth/types";
import { PlanChangeCard } from "./AccountPlanChange";

/**
 * The Change plan control end to end (ADR-0029): the card's states, the role hint, the form →
 * server-computed confirm → announced result flow, the idempotency key's lifetime, a double click
 * sending once, every failure the server can answer with, and that focus lands back on the control
 * that opened the dialog.
 */

const ORG: PricingOrgRef = { organization_id: "org_test", name: "Zenith Polymers" };

function product(overrides: Partial<ProductPricing> = {}): ProductPricing {
  return {
    product_key: "hrms",
    product_name: "OpsGrids HR",
    subscription: {
      id: "sub_1",
      state: "active",
      plan_version_id: "pv_growth",
      plan_name: "Growth",
      trial_ends_at: null,
      current_period_end: "2026-11-01T00:00:00Z",
      pending_plan_version_id: null,
      pending_plan_name: null,
      cancel_at: null,
      entitled_products: ["hrms"],
    },
    current_price: {
      plan_version_id: "pv_growth",
      is_bespoke: false,
      amount_minor: 4_500_000,
      currency: "INR",
      billing_interval: "month",
    },
    public_price: null,
    history: [],
    ...overrides,
  };
}

function pricing(...products: ProductPricing[]): OrganizationPricing {
  return { organization_id: "org_test", products };
}

function option(overrides: Partial<PlanOption>): PlanOption {
  return {
    plan_id: "pl_x",
    plan_name: "Plan",
    plan_version_id: "pv_x",
    version_no: 1,
    billing_interval: "month",
    currency: "INR",
    is_bespoke: false,
    recurring_minor: 100_000,
    prices: [{ kind: "flat", amount_minor: 100_000, currency: "INR", unit: null, unit_price_micros: null, meter_key: null }],
    bundled_products: null,
    trial_days: null,
    is_current: false,
    is_pending: false,
    blocked_code: null,
    blocked_reason: null,
    ...overrides,
  };
}

function options(overrides: Partial<PlanOptions> = {}): PlanOptions {
  return {
    organization_id: "org_test",
    product_key: "hrms",
    subscription: {
      id: "sub_1",
      state: "active",
      plan_version_id: "pv_growth",
      plan_name: "Growth",
      pending_plan_version_id: null,
      pending_plan_name: null,
      currency: "INR",
      billing_interval: "month",
      trial_ends_at: null,
      current_period_end: "2026-11-01T00:00:00Z",
      change_blocked_code: null,
    },
    options: [
      option({ plan_name: "Growth", plan_version_id: "pv_growth", version_no: 3, is_current: true, recurring_minor: 4_500_000 }),
      option({ plan_name: "Scale", plan_version_id: "pv_scale", version_no: 2, recurring_minor: 12_000_000 }),
      option({ plan_name: "Starter", plan_version_id: "pv_starter", version_no: 2, recurring_minor: 1_800_000 }),
      option({
        plan_name: "Yearly",
        plan_version_id: "pv_yearly",
        billing_interval: "year",
        blocked_code: "cadence",
        blocked_reason: "this subscription renews every month; a year plan cannot replace it mid-subscription",
      }),
      option({
        plan_name: "Dollar",
        plan_version_id: "pv_dollar",
        currency: "USD",
        blocked_code: "currency",
        blocked_reason: "this subscription is billed in INR; this plan is in USD",
      }),
    ],
    ...overrides,
  };
}

function preview(overrides: Partial<PlanChangePreview> = {}): PlanChangePreview {
  return {
    expected: { state: "active", plan_version_id: "pv_growth", pending_plan_version_id: null },
    action: "switch",
    direction: "upgrade",
    effective: "next_period",
    effective_at: "2026-11-01T00:00:00Z",
    would_clear_pending: false,
    entitled_added: [],
    entitled_removed: [],
    usage_priced: false,
    refusal_code: null,
    refusal_detail: null,
    ...overrides,
  };
}

const SCHEDULED: PlanChangeResult = {
  effective: "next_period",
  state: "active",
  plan_version_id: "pv_growth",
  pending_plan_version_id: "pv_scale",
  effective_at: "2026-11-01T00:00:00Z",
  replayed: false,
};

function problem(status: number, code: string | null, detail: string): ApiError {
  return new ApiError(status, detail, {
    status,
    detail,
    request_id: "req-1234",
    ...(code ? { errors: [{ code, message: detail }] } : {}),
  });
}

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

interface Wired {
  getPricing: MockInstance<typeof client.getOrganizationPricing>;
  getOptions: MockInstance<typeof client.getPlanOptions>;
  previewSpy: MockInstance<typeof client.previewPlanChange>;
  changeSpy: MockInstance<typeof client.changePlan>;
}

function wire(opts: { products?: ProductPricing[] } = {}): Wired {
  return {
    getPricing: vi.spyOn(client, "getOrganizationPricing").mockResolvedValue(pricing(...(opts.products ?? [product()]))),
    getOptions: vi.spyOn(client, "getPlanOptions").mockResolvedValue(options()),
    previewSpy: vi.spyOn(client, "previewPlanChange").mockResolvedValue(preview()),
    changeSpy: vi.spyOn(client, "changePlan").mockResolvedValue(SCHEDULED),
  };
}

async function openDialog() {
  const trigger = await screen.findByRole("button", { name: "Change plan for OpsGrids HR" });
  await userEvent.click(trigger);
  const dialog = await screen.findByRole("dialog", { name: /Change plan — OpsGrids HR/ });
  await within(dialog).findByRole("radio", { name: /Scale/ });
  return { trigger, dialog };
}

async function fillForm(dialog: HTMLElement, plan: RegExp = /Scale/, reason = "Agreed on the renewal call") {
  await userEvent.click(within(dialog).getByRole("radio", { name: plan }));
  await userEvent.type(within(dialog).getByRole("textbox", { name: /Reason/ }), reason);
}

beforeEach(() => {
  mockAuth(["billing_ops"]);
});

afterEach(() => {
  vi.restoreAllMocks();
});

/* ------------------------------------------------------------------------- the card */

describe("the card", () => {
  it("shows a skeleton, then each subscribed product with its plan and a Change plan button", async () => {
    wire({ products: [product(), product({ product_key: "crm", product_name: "OpsGrids CRM", subscription: null })] });
    render(<PlanChangeCard org={ORG} />);
    expect(screen.getByText("Change plan")).toBeTruthy();
    expect(await screen.findByText("OpsGrids HR")).toBeTruthy();
    expect(screen.getByText("Growth")).toBeTruthy();
    // A product with no subscription has nothing to change.
    expect(screen.queryByText("OpsGrids CRM")).toBeNull();
    expect((await screen.findByRole("button", { name: "Change plan for OpsGrids HR" })).hasAttribute("disabled")).toBe(false);
  });

  it("shows a scheduled change, and where it lands, beside the current plan", async () => {
    wire({
      products: [
        product({
          subscription: {
            ...(product().subscription as NonNullable<ProductPricing["subscription"]>),
            pending_plan_version_id: "pv_scale",
            pending_plan_name: "Scale",
          },
        }),
      ],
    });
    render(<PlanChangeCard org={ORG} />);
    expect(await screen.findByText(/Scheduled: Scale on 01 Nov 2026 \(UTC\)/)).toBeTruthy();
  });

  it("renders the error state verbatim and retries on demand", async () => {
    const w = wire();
    w.getPricing.mockRejectedValueOnce(new Error("catalog is unavailable")).mockResolvedValueOnce(pricing(product()));
    render(<PlanChangeCard org={ORG} />);
    expect(await screen.findByText("catalog is unavailable")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("OpsGrids HR")).toBeTruthy();
    expect(w.getPricing).toHaveBeenCalledTimes(2);
  });

  it("says there is nothing to change when nothing is subscribed", async () => {
    wire({ products: [product({ subscription: null })] });
    render(<PlanChangeCard org={ORG} />);
    expect(await screen.findByText("No subscriptions to change")).toBeTruthy();
  });
});

describe("role and state hints", () => {
  it("disables the button for support, and says who can", async () => {
    mockAuth(["support"]);
    wire();
    render(<PlanChangeCard org={ORG} />);
    const button = await screen.findByRole("button", { name: "Change plan for OpsGrids HR" });
    expect((button as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getAllByText("Owners and billing ops can change a plan.").length).toBeGreaterThan(0);
  });

  it.each([["owner"], ["billing_ops"]] as const)("enables it for %s", async (role) => {
    mockAuth([role]);
    wire();
    render(<PlanChangeCard org={ORG} />);
    const button = await screen.findByRole("button", { name: "Change plan for OpsGrids HR" });
    expect((button as HTMLButtonElement).disabled).toBe(false);
  });

  it.each(["past_due", "suspended", "cancelled"])("disables it for a %s subscription, with the reason", async (state) => {
    wire({ products: [product({ subscription: { ...(product().subscription as NonNullable<ProductPricing["subscription"]>), state } })] });
    render(<PlanChangeCard org={ORG} />);
    const button = await screen.findByRole("button", { name: "Change plan for OpsGrids HR" });
    expect((button as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText("Only a trial or active subscription can change plan.")).toBeTruthy();
  });
});

/* ------------------------------------------------------------------------- the form */

describe("the form", () => {
  it("lists the choices with the current plan and blocked plans disabled, and says why", async () => {
    wire();
    render(<PlanChangeCard org={ORG} />);
    const { dialog } = await openDialog();

    expect((within(dialog).getByRole("radio", { name: /Growth/ }) as HTMLInputElement).disabled).toBe(true);
    expect(within(dialog).getByText("Current plan")).toBeTruthy();
    expect((within(dialog).getByRole("radio", { name: /Scale/ }) as HTMLInputElement).disabled).toBe(false);
    expect((within(dialog).getByRole("radio", { name: /Starter/ }) as HTMLInputElement).disabled).toBe(false);
    const yearly = within(dialog).getByRole("radio", { name: /Yearly/ }) as HTMLInputElement;
    expect(yearly.disabled).toBe(true);
    expect(within(dialog).getByText(/a year plan cannot replace it mid-subscription/)).toBeTruthy();
    expect((within(dialog).getByRole("radio", { name: /Dollar/ }) as HTMLInputElement).disabled).toBe(true);
    expect(within(dialog).getByText("this subscription is billed in INR; this plan is in USD")).toBeTruthy();
    // Prices come from integer minor units through the shared formatter.
    expect(within(dialog).getByText(/₹1,20,000\.00 per month/)).toBeTruthy();
  });

  it("is a labelled group with a required, labelled reason", async () => {
    wire();
    render(<PlanChangeCard org={ORG} />);
    const { dialog } = await openDialog();
    expect(within(dialog).getByRole("group", { name: "Move to" })).toBeTruthy();
    expect(within(dialog).getByRole("textbox", { name: /Reason/ })).toBeTruthy();
    expect(within(dialog).getByText(/A trial switches now; a paying subscription switches at its next billing period/)).toBeTruthy();
  });

  it("refuses to continue without a plan and a reason, naming each, and calls nothing", async () => {
    const w = wire();
    render(<PlanChangeCard org={ORG} />);
    const { dialog } = await openDialog();
    await userEvent.click(within(dialog).getByRole("button", { name: "Review change" }));
    expect(await within(dialog).findByText("Choose the plan to move to.")).toBeTruthy();
    expect(within(dialog).getByText("Enter a reason.")).toBeTruthy();
    expect(w.previewSpy).not.toHaveBeenCalled();
  });

  it("explains an empty offer rather than showing an empty list", async () => {
    const w = wire();
    w.getOptions.mockResolvedValue(options({ options: [] }));
    render(<PlanChangeCard org={ORG} />);
    const trigger = await screen.findByRole("button", { name: "Change plan for OpsGrids HR" });
    await userEvent.click(trigger);
    expect(await screen.findByText(/No other plan is on offer for this product/)).toBeTruthy();
  });

  it("shows a failed options read in the dialog with a retry", async () => {
    const w = wire();
    w.getOptions.mockRejectedValueOnce(problem(503, null, "catalog is unavailable")).mockResolvedValueOnce(options());
    render(<PlanChangeCard org={ORG} />);
    await userEvent.click(await screen.findByRole("button", { name: "Change plan for OpsGrids HR" }));
    const dialog = await screen.findByRole("dialog", { name: /Change plan — OpsGrids HR/ });
    expect(await within(dialog).findByText("catalog is unavailable")).toBeTruthy();
    expect(within(dialog).getByText(/Reference: req-1234/)).toBeTruthy();
    await userEvent.click(within(dialog).getByRole("button", { name: "Try again" }));
    expect(await within(dialog).findByRole("radio", { name: /Scale/ })).toBeTruthy();
  });

  it("warns that a subscription that cannot change plan cannot", async () => {
    const w = wire();
    w.getOptions.mockResolvedValue(
      options({ subscription: { ...options().subscription, state: "past_due", change_blocked_code: "state" } }),
    );
    render(<PlanChangeCard org={ORG} />);
    await userEvent.click(await screen.findByRole("button", { name: "Change plan for OpsGrids HR" }));
    expect(await screen.findByText("A past_due subscription cannot change plan.")).toBeTruthy();
  });
});

/* ----------------------------------------------------------------- preview and confirm */

describe("the preview and the confirm", () => {
  it("asks the server what would happen, and says it in a sentence with no amount", async () => {
    const w = wire();
    render(<PlanChangeCard org={ORG} />);
    const { dialog } = await openDialog();
    await fillForm(dialog);
    await userEvent.click(within(dialog).getByRole("button", { name: "Review change" }));

    const confirm = await screen.findByRole("dialog", { name: "Confirm plan change" });
    expect(w.previewSpy).toHaveBeenCalledWith("org_test", "hrms", { plan_version_id: "pv_scale", timing: "auto" });
    expect(
      within(confirm).getByText(
        "Zenith Polymers stays on Growth until 01 Nov 2026 (UTC), then moves to Scale (a price increase). Nothing changes, and nothing is charged, before then.",
      ),
    ).toBeTruthy();
    expect(within(confirm).getByText(/Takes effect at the end of the current billing period — 01 Nov 2026 \(UTC\)/)).toBeTruthy();
    expect(within(confirm).getByText("Agreed on the renewal call")).toBeTruthy();
    // The ~15 minute entitlement lag and the billing-run dependency are said before, not after.
    expect(within(confirm).getByText(/within about 15 minutes/)).toBeTruthy();
    expect(within(confirm).getByText(/applied by the billing run/)).toBeTruthy();
    expect(confirm.textContent).not.toMatch(/prorat|charged now/i);
    expect(w.changeSpy).not.toHaveBeenCalled();
  });

  it("names what a bundle move adds and removes, what it replaces, and a usage price", async () => {
    const w = wire();
    w.previewSpy.mockResolvedValue(
      preview({ entitled_added: ["finance"], entitled_removed: ["crm"], would_clear_pending: true, usage_priced: true }),
    );
    render(<PlanChangeCard org={ORG} />);
    const { dialog } = await openDialog();
    await fillForm(dialog);
    await userEvent.click(within(dialog).getByRole("button", { name: "Review change" }));
    const confirm = await screen.findByRole("dialog", { name: "Confirm plan change" });
    expect(within(confirm).getByText("Adds access to: finance.")).toBeTruthy();
    expect(within(confirm).getByText("Removes access to: crm.")).toBeTruthy();
    expect(within(confirm).getByText("This replaces the change already scheduled.")).toBeTruthy();
    expect(within(confirm).getByText(/usage-based price is involved/)).toBeTruthy();
  });

  it("a trial's confirm says it switches now and nothing is charged", async () => {
    const w = wire();
    w.previewSpy.mockResolvedValue(preview({ effective: "now", effective_at: null }));
    render(<PlanChangeCard org={ORG} />);
    const { dialog } = await openDialog();
    await fillForm(dialog);
    await userEvent.click(within(dialog).getByRole("button", { name: "Review change" }));
    const confirm = await screen.findByRole("dialog", { name: "Confirm plan change" });
    expect(within(confirm).getByText(/moves from Growth to Scale now/)).toBeTruthy();
    expect(within(confirm).queryByText(/applied by the billing run/)).toBeNull();
  });

  it("Back returns to the form with the choice and the reason kept", async () => {
    wire();
    render(<PlanChangeCard org={ORG} />);
    const { dialog } = await openDialog();
    await fillForm(dialog);
    await userEvent.click(within(dialog).getByRole("button", { name: "Review change" }));
    const confirm = await screen.findByRole("dialog", { name: "Confirm plan change" });
    await userEvent.click(within(confirm).getByRole("button", { name: "Back" }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Confirm plan change" })).toBeNull());
    expect((within(dialog).getByRole("radio", { name: /Scale/ }) as HTMLInputElement).checked).toBe(true);
    expect((within(dialog).getByRole("textbox", { name: /Reason/ }) as HTMLTextAreaElement).value).toBe(
      "Agreed on the renewal call",
    );
  });

  it("a refusal from the preview is shown on the form in the server's words, and offers no confirm", async () => {
    const w = wire();
    w.previewSpy.mockResolvedValue(
      preview({
        direction: null,
        effective: null,
        effective_at: null,
        refusal_code: "cadence",
        refusal_detail: "cannot change from a month plan to a year one mid-subscription",
      }),
    );
    render(<PlanChangeCard org={ORG} />);
    const { dialog } = await openDialog();
    await fillForm(dialog);
    await userEvent.click(within(dialog).getByRole("button", { name: "Review change" }));
    expect(await within(dialog).findByText("cannot change from a month plan to a year one mid-subscription")).toBeTruthy();
    expect(within(dialog).getByText(/keeps one billing cadence/)).toBeTruthy();
    expect(screen.queryByRole("dialog", { name: "Confirm plan change" })).toBeNull();
    expect(w.changeSpy).not.toHaveBeenCalled();
  });

  it("a failed preview is shown on the form with its reference", async () => {
    const w = wire();
    w.previewSpy.mockRejectedValue(problem(503, null, "subscription is unavailable"));
    render(<PlanChangeCard org={ORG} />);
    const { dialog } = await openDialog();
    await fillForm(dialog);
    await userEvent.click(within(dialog).getByRole("button", { name: "Review change" }));
    expect(await within(dialog).findByText("subscription is unavailable")).toBeTruthy();
    expect(within(dialog).getByText(/Reference: req-1234/)).toBeTruthy();
  });
});

/* ------------------------------------------------------------------------ the confirm */

describe("confirming", () => {
  async function toConfirm() {
    const w = wire();
    render(<PlanChangeCard org={ORG} />);
    const { dialog, trigger } = await openDialog();
    await fillForm(dialog, /Scale/, "  Agreed on the renewal call  ");
    await userEvent.click(within(dialog).getByRole("button", { name: "Review change" }));
    const confirm = await screen.findByRole("dialog", { name: "Confirm plan change" });
    return { w, dialog, confirm, trigger };
  }

  it("sends the target, the trimmed reason, the preview's own expected state and one key", async () => {
    const { w, confirm } = await toConfirm();
    await userEvent.click(within(confirm).getByRole("button", { name: "Confirm change" }));
    await waitFor(() => expect(w.changeSpy).toHaveBeenCalledTimes(1));
    const [orgId, productKey, request, key] = w.changeSpy.mock.calls[0] as [string, string, PlanChangeRequest, string];
    expect([orgId, productKey]).toEqual(["org_test", "hrms"]);
    expect(request).toEqual({
      plan_version_id: "pv_scale",
      timing: "auto",
      reason: "Agreed on the renewal call",
      expected: { state: "active", plan_version_id: "pv_growth", pending_plan_version_id: null },
    });
    expect(key).toMatch(/^[0-9a-f-]{36}$/);
  });

  it("there is no way to ask for an immediate change: timing is never 'now'", async () => {
    const { w, confirm } = await toConfirm();
    await userEvent.click(within(confirm).getByRole("button", { name: "Confirm change" }));
    await waitFor(() => expect(w.changeSpy).toHaveBeenCalled());
    const request = w.changeSpy.mock.calls[0]?.[2] as { timing: string };
    expect(["auto", "next_period"]).toContain(request.timing);
  });

  it("a double click sends exactly once", async () => {
    const { w, confirm } = await toConfirm();
    let resolve: (value: PlanChangeResult) => void = () => undefined;
    w.changeSpy.mockImplementation(() => new Promise<PlanChangeResult>((r) => (resolve = r)));
    await userEvent.dblClick(within(confirm).getByRole("button", { name: "Confirm change" }));
    expect(w.changeSpy).toHaveBeenCalledTimes(1);
    resolve(SCHEDULED);
    await screen.findByText("Change scheduled");
    expect(w.changeSpy).toHaveBeenCalledTimes(1);
  });

  it("announces the result in a polite live region, closes the dialogs and reloads quietly", async () => {
    const { w, confirm, trigger } = await toConfirm();
    await userEvent.click(within(confirm).getByRole("button", { name: "Confirm change" }));

    const region = await screen.findByRole("status");
    expect(region.getAttribute("aria-live")).toBe("polite");
    await waitFor(() => expect(within(region).getByText("Change scheduled")).toBeTruthy());
    expect(within(region).getByText(/Zenith Polymers moves to Scale on 01 Nov 2026 \(UTC\)/)).toBeTruthy();
    expect(within(region).getByText(/within about 15 minutes/)).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();
    // The card reloaded the live read, and the control that opened the dialog is still the same
    // element on the page — which is what lets focus return to it.
    await waitFor(() => expect(w.getPricing).toHaveBeenCalledTimes(2));
    expect(screen.getByRole("button", { name: "Change plan for OpsGrids HR" })).toBe(trigger);
    await waitFor(() => expect(document.activeElement).toBe(trigger));
  });

  it("the live region can be dismissed", async () => {
    const { confirm } = await toConfirm();
    await userEvent.click(within(confirm).getByRole("button", { name: "Confirm change" }));
    await screen.findByText("Change scheduled");
    await userEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByText("Change scheduled")).toBeNull();
  });

  it("returns focus to the opening control when the flow is cancelled", async () => {
    wire();
    render(<PlanChangeCard org={ORG} />);
    const { dialog, trigger } = await openDialog();
    await userEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await waitFor(() => expect(document.activeElement).toBe(trigger));
  });
});

/* -------------------------------------------------------------------------- failures */

describe("a failed confirm", () => {
  async function toConfirm() {
    const w = wire();
    render(<PlanChangeCard org={ORG} />);
    const { dialog } = await openDialog();
    await fillForm(dialog);
    await userEvent.click(within(dialog).getByRole("button", { name: "Review change" }));
    const confirm = await screen.findByRole("dialog", { name: "Confirm plan change" });
    return { w, dialog, confirm };
  }

  it.each([
    [409, "state_changed", "this subscription changed since you looked at it; refresh and review it again", /not the way it was when you opened this/],
    [409, "billing_catching_up", "billing is catching up on this subscription — try again shortly", /does not clear by itself/],
    [422, "state", "a past_due subscription cannot change plan; only a trial or an active one can", /Only a trial or an active subscription/],
    [422, "reserved_reason", "'renew' is a reason only the platform's own machinery records", /in your own words/],
  ] as const)(
    "a %s %s refusal returns to the form with the server's sentence, its reference and next step",
    async (status, code, detail, guidance) => {
      const { w, dialog } = await toConfirm();
      w.changeSpy.mockRejectedValue(problem(status, code, detail));
      await userEvent.click(within(screen.getByRole("dialog", { name: "Confirm plan change" })).getByRole("button", { name: "Confirm change" }));

      expect(await within(dialog).findByText(detail)).toBeTruthy();
      expect(within(dialog).getByText(/Reference: req-1234/)).toBeTruthy();
      expect(within(dialog).getByText(guidance)).toBeTruthy();
      expect(screen.queryByRole("dialog", { name: "Confirm plan change" })).toBeNull();
      // The live state is read again behind it — the picture the operator had is stale.
      await waitFor(() => expect(w.getOptions).toHaveBeenCalledTimes(2));
      await waitFor(() => expect(w.getPricing.mock.calls.length).toBeGreaterThanOrEqual(2));
    },
  );

  it("mints a new key after a refusal, so the next attempt is a new request", async () => {
    const { w, dialog } = await toConfirm();
    w.changeSpy.mockRejectedValueOnce(problem(409, "state_changed", "changed")).mockResolvedValueOnce(SCHEDULED);
    await userEvent.click(within(screen.getByRole("dialog", { name: "Confirm plan change" })).getByRole("button", { name: "Confirm change" }));
    await within(dialog).findByText("changed");

    // The options reload clears nothing the operator typed.
    await within(dialog).findByRole("radio", { name: /Scale/ });
    await userEvent.click(within(dialog).getByRole("button", { name: "Review change" }));
    const second = await screen.findByRole("dialog", { name: "Confirm plan change" });
    await userEvent.click(within(second).getByRole("button", { name: "Confirm change" }));
    await waitFor(() => expect(w.changeSpy).toHaveBeenCalledTimes(2));
    const keys = w.changeSpy.mock.calls.map((c) => c[3]);
    expect(keys[0]).not.toBe(keys[1]);
  });

  it.each([
    ["a 503", problem(503, "outcome_unknown", "subscription did not answer, so the change may or may not have been made.")],
    ["a 502", problem(502, null, "Bad gateway")],
    ["a dropped connection", new TypeError("Failed to fetch")],
  ])("%s keeps the confirm open, says the outcome is unknown, and Try again reuses the same key", async (_label, failure) => {
    const { w, confirm } = await toConfirm();
    w.changeSpy.mockRejectedValueOnce(failure).mockResolvedValueOnce(SCHEDULED);
    await userEvent.click(within(confirm).getByRole("button", { name: "Confirm change" }));

    // The server's (or the network's) own sentence, then what to do about an unknown outcome.
    expect(await within(confirm).findByText(String((failure as Error).message))).toBeTruthy();
    expect(within(confirm).getByText(/Refresh this account to check/)).toBeTruthy();
    // The live state is refreshed behind the dialog, which stays put.
    await waitFor(() => expect(w.getPricing.mock.calls.length).toBeGreaterThanOrEqual(2));
    expect(screen.getByRole("dialog", { name: "Confirm plan change" })).toBeTruthy();

    await userEvent.click(within(confirm).getByRole("button", { name: "Try again" }));
    await screen.findByText("Change scheduled");
    expect(w.changeSpy).toHaveBeenCalledTimes(2);
    expect(w.changeSpy.mock.calls[0]?.[3]).toBe(w.changeSpy.mock.calls[1]?.[3]);
  });

  it("a request already in flight is reported, and does not mint a new key", async () => {
    const { w, confirm } = await toConfirm();
    w.changeSpy.mockRejectedValue(problem(409, "request_in_progress", "this request is already being processed"));
    await userEvent.click(within(confirm).getByRole("button", { name: "Confirm change" }));
    expect(await within(confirm).findByText("this request is already being processed")).toBeTruthy();
    expect(screen.getByRole("dialog", { name: "Confirm plan change" })).toBeTruthy();
  });
});

/* --------------------------------------------------------------------------- reloads */

describe("reloading", () => {
  it("a reload while the dialog is open does not unmount it or what was typed", async () => {
    const w = wire();
    render(<PlanChangeCard org={ORG} />);
    const { dialog } = await openDialog();
    await fillForm(dialog);
    // The card reloads its live read (as an unknown outcome elsewhere would trigger).
    w.previewSpy.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    await userEvent.click(within(dialog).getByRole("button", { name: "Review change" }));
    expect(screen.getByRole("dialog", { name: /Change plan — OpsGrids HR/ })).toBeTruthy();
    expect((within(dialog).getByRole("textbox", { name: /Reason/ }) as HTMLTextAreaElement).value).toBe(
      "Agreed on the renewal call",
    );
  });
});
