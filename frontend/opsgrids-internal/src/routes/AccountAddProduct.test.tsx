import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { MockInstance } from "vitest";

import * as client from "@/api/client";
import { ApiError } from "@/api/http";
import type { AddProductOption, AddProductOptions, AddProductRequest, AddProductResult, PricingOrgRef } from "@/api/types";
import * as authContext from "@/auth/context";
import type { AuthContextValue } from "@/auth/context";
import type { OperatorRole } from "@/auth/types";
import { AddProductCard } from "./AccountAddProduct";

/**
 * Add product end to end (ADR-0030): the role hint, the picker (only products with no row, blocked
 * versions disabled with the server's reason, a bundle's overlap named), a trial by default, a paid
 * start that cannot be confirmed without the acknowledgement, the confirm → announced result, the
 * idempotency key's lifetime, a double click sending once, and every failure kind.
 */

const ORG: PricingOrgRef = { organization_id: "org_test", name: "Zenith Polymers" };

function version(overrides: Partial<AddProductOption> = {}): AddProductOption {
  return {
    plan_id: "pl_x",
    plan_name: "Growth",
    plan_version_id: "pv_growth",
    version_no: 2,
    billing_interval: "month",
    currency: "INR",
    is_bespoke: false,
    recurring_minor: 99_900,
    prices: [{ kind: "flat", amount_minor: 99_900, currency: "INR", unit: null, unit_price_micros: null, meter_key: null }],
    bundled_products: null,
    trial_days: 21,
    offers_trial: true,
    overlapping_products: [],
    blocked_code: null,
    blocked_reason: null,
    ...overrides,
  };
}

function options(overrides: Partial<AddProductOptions> = {}): AddProductOptions {
  return {
    organization_id: "org_test",
    currency: "INR",
    existing: [
      { product_key: "hrms", state: "active" },
      { product_key: "auctions", state: "cancelled" },
    ],
    products: [
      {
        product_key: "crm",
        product_name: "OpsGrids CRM",
        entitled_through: [],
        versions: [
          version(),
          version({ plan_name: "Starter", plan_version_id: "pv_starter", offers_trial: false, trial_days: 0 }),
          version({
            plan_name: "Global",
            plan_version_id: "pv_usd",
            currency: "USD",
            blocked_code: "org_currency_mismatch",
            blocked_reason: "this organization is billed in INR; this plan is in USD",
          }),
        ],
      },
      {
        product_key: "suite",
        product_name: "OpsGrids Suite",
        entitled_through: [],
        versions: [
          version({
            plan_name: "Suite Duo",
            plan_version_id: "pv_duo",
            bundled_products: ["hrms", "crm"],
            overlapping_products: ["hrms"],
          }),
        ],
      },
    ],
    ...overrides,
  };
}

const TRIAL_RESULT: AddProductResult = {
  subscription_id: "sub_new",
  product_key: "crm",
  state: "trial",
  plan_version_id: "pv_growth",
  currency: "INR",
  trial_ends_at: "2026-10-27T09:00:00Z",
  current_period_start: "2026-10-06T09:00:00Z",
  current_period_end: "2026-11-05T09:00:00Z",
  entitled_products: ["crm"],
  charge_claimed: false,
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
  getOptions: MockInstance<typeof client.getAddProductOptions>;
  addSpy: MockInstance<typeof client.addProduct>;
}

function wire(): Wired {
  return {
    getOptions: vi.spyOn(client, "getAddProductOptions").mockResolvedValue(options()),
    addSpy: vi.spyOn(client, "addProduct").mockResolvedValue(TRIAL_RESULT),
  };
}

async function openDialog() {
  const trigger = await screen.findByRole("button", { name: "Add product" });
  await userEvent.click(trigger);
  const dialog = await screen.findByRole("dialog", { name: "Add product" });
  await within(dialog).findByRole("combobox", { name: /Product/ });
  return { trigger, dialog };
}

async function fill(dialog: HTMLElement, opts: { product?: string; plan?: RegExp; reason?: string } = {}) {
  await userEvent.selectOptions(within(dialog).getByRole("combobox", { name: /Product/ }), opts.product ?? "crm");
  await userEvent.click(within(dialog).getByRole("radio", { name: opts.plan ?? /Growth/ }));
  await userEvent.type(within(dialog).getByRole("textbox", { name: /Reason/ }), opts.reason ?? "Onboarding the sales team");
}

async function toConfirm(w: Wired) {
  render(<AddProductCard org={ORG} />);
  const { dialog, trigger } = await openDialog();
  await fill(dialog);
  await userEvent.click(within(dialog).getByRole("button", { name: "Review" }));
  const confirm = await screen.findByRole("dialog", { name: "Confirm new subscription" });
  return { w, dialog, confirm, trigger };
}

beforeEach(() => {
  mockAuth(["billing_ops"]);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("the card and its role hint", () => {
  it.each([["owner"], ["billing_ops"]] as const)("enables Add product for %s", async (role) => {
    mockAuth([role]);
    wire();
    render(<AddProductCard org={ORG} />);
    expect((screen.getByRole("button", { name: "Add product" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("disables it for support, and says who can", () => {
    mockAuth(["support"]);
    const w = wire();
    render(<AddProductCard org={ORG} />);
    const button = screen.getByRole("button", { name: "Add product" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(screen.getByText("Owners and billing ops can add a product.")).toBeTruthy();
    expect(button.getAttribute("aria-describedby")).toBeTruthy();
    expect(w.getOptions).not.toHaveBeenCalled();
  });
});

describe("the form", () => {
  it("offers only products with no row, says which are not offered, and lists blocked plans disabled", async () => {
    wire();
    render(<AddProductCard org={ORG} />);
    const { dialog } = await openDialog();
    const select = within(dialog).getByRole("combobox", { name: /Product/ });
    const choices = within(select).getAllByRole("option").map((o) => o.textContent);
    expect(choices).toEqual(["Choose a product", "OpsGrids CRM", "OpsGrids Suite"]);
    expect(within(dialog).getByText(/Not offered — already has: hrms \(active\), auctions \(cancelled\)/)).toBeTruthy();

    await userEvent.selectOptions(select, "crm");
    expect((within(dialog).getByRole("radio", { name: /Global/ }) as HTMLInputElement).disabled).toBe(true);
    expect(within(dialog).getByText("this organization is billed in INR; this plan is in USD")).toBeTruthy();
    expect(within(dialog).getByText(/₹999\.00 per month · 21-day trial/)).toBeTruthy();
  });

  it("says the reason stays with staff, never on the customer org's event", async () => {
    wire();
    render(<AddProductCard org={ORG} />);
    const { dialog } = await openDialog();
    expect(within(dialog).getByText("Recorded on the staff audit trail only. Not sent to the customer.")).toBeTruthy();
    expect(dialog.textContent).not.toMatch(/subscription's event/);
  });

  it("starts as a trial by default", async () => {
    wire();
    render(<AddProductCard org={ORG} />);
    const { dialog } = await openDialog();
    expect((within(dialog).getByRole("radio", { name: /^Trial/ }) as HTMLInputElement).checked).toBe(true);
    expect(within(dialog).queryByRole("checkbox")).toBeNull();
  });

  it("refuses to continue without a product, a plan and a reason, and calls nothing", async () => {
    const w = wire();
    render(<AddProductCard org={ORG} />);
    const { dialog } = await openDialog();
    await userEvent.click(within(dialog).getByRole("button", { name: "Review" }));
    expect(await within(dialog).findByText("Choose a product.")).toBeTruthy();
    expect(within(dialog).getByText("Enter a reason.")).toBeTruthy();
    expect(w.addSpy).not.toHaveBeenCalled();
  });

  it("will not confirm a paid start until the charge is acknowledged", async () => {
    const w = wire();
    render(<AddProductCard org={ORG} />);
    const { dialog } = await openDialog();
    await fill(dialog);
    await userEvent.click(within(dialog).getByRole("radio", { name: /Paid from today/ }));
    const checkbox = within(dialog).getByRole("checkbox", { name: /billing starts now/ });
    await userEvent.click(within(dialog).getByRole("button", { name: "Review" }));
    expect(await within(dialog).findByText("Confirm that billing starts now.")).toBeTruthy();
    expect(screen.queryByRole("dialog", { name: "Confirm new subscription" })).toBeNull();

    await userEvent.click(checkbox);
    await userEvent.click(within(dialog).getByRole("button", { name: "Review" }));
    const confirm = await screen.findByRole("dialog", { name: "Confirm new subscription" });
    expect(within(confirm).getByText(/no payment mandate is checked first/)).toBeTruthy();
    await userEvent.click(within(confirm).getByRole("button", { name: "Add and start billing" }));
    await waitFor(() => expect(w.addSpy).toHaveBeenCalledTimes(1));
    const request = w.addSpy.mock.calls[0]?.[1] as AddProductRequest;
    expect(request.trial).toBe(false);
    expect(request.acknowledge_immediate_charge).toBe(true);
  });

  it("a plan that sells no trial cannot be started as one", async () => {
    wire();
    render(<AddProductCard org={ORG} />);
    const { dialog } = await openDialog();
    await fill(dialog, { plan: /Starter/ });
    expect((within(dialog).getByRole("radio", { name: /^Trial/ }) as HTMLInputElement).disabled).toBe(true);
    await userEvent.click(within(dialog).getByRole("button", { name: "Review" }));
    expect(await within(dialog).findByText(/This plan sells no trial/)).toBeTruthy();
  });

  it("warns that a bundle bills alongside a member already paid for", async () => {
    wire();
    render(<AddProductCard org={ORG} />);
    const { dialog } = await openDialog();
    await fill(dialog, { product: "suite", plan: /Suite Duo/ });
    expect(within(dialog).getByText("Already paid for separately")).toBeTruthy();
    expect(within(dialog).getByText(/Adding this bundle bills both, with no credit/)).toBeTruthy();
    expect(within(dialog).queryByText("Already included in a bundle")).toBeNull();
  });

  it("warns, in the form and the confirm, that a live bundle already includes the product", async () => {
    const base = options();
    const [crm, suite] = base.products;
    if (!crm || !suite) throw new Error("fixture");
    vi.spyOn(client, "getAddProductOptions").mockResolvedValue({
      ...base,
      existing: [{ product_key: "suite", state: "active" }],
      products: [{ ...crm, entitled_through: ["suite"] }],
    });
    const addSpy = vi.spyOn(client, "addProduct").mockResolvedValue(TRIAL_RESULT);
    render(<AddProductCard org={ORG} />);
    const { dialog } = await openDialog();
    expect(within(dialog).queryByText("Already included in a bundle")).toBeNull();
    await fill(dialog);
    expect(within(dialog).getByText("Already included in a bundle")).toBeTruthy();
    expect(
      within(dialog).getByText(/already gets OpsGrids CRM through its suite subscription\. Adding it on its own bills both, with no credit/),
    ).toBeTruthy();
    expect(within(dialog).queryByText("Already paid for separately")).toBeNull();

    await userEvent.click(within(dialog).getByRole("button", { name: "Review" }));
    const confirm = await screen.findByRole("dialog", { name: "Confirm new subscription" });
    expect(within(confirm).getByText(/Bills alongside the suite subscription, which already includes OpsGrids CRM/)).toBeTruthy();
    await userEvent.click(within(confirm).getByRole("button", { name: "Start trial" }));
    await waitFor(() => expect(addSpy).toHaveBeenCalledTimes(1));
  });

  it("explains an organization with nothing left to add", async () => {
    const w = wire();
    w.getOptions.mockResolvedValue(options({ products: [] }));
    render(<AddProductCard org={ORG} />);
    await userEvent.click(screen.getByRole("button", { name: "Add product" }));
    expect(await screen.findByText(/There is nothing to add/)).toBeTruthy();
  });

  it("shows a failed options read with its reference and a retry", async () => {
    const w = wire();
    w.getOptions.mockRejectedValueOnce(problem(503, null, "catalog is unavailable")).mockResolvedValueOnce(options());
    render(<AddProductCard org={ORG} />);
    await userEvent.click(screen.getByRole("button", { name: "Add product" }));
    const dialog = await screen.findByRole("dialog", { name: "Add product" });
    expect(await within(dialog).findByText("catalog is unavailable")).toBeTruthy();
    expect(within(dialog).getByText(/Reference: req-1234/)).toBeTruthy();
    await userEvent.click(within(dialog).getByRole("button", { name: "Try again" }));
    expect(await within(dialog).findByRole("combobox", { name: /Product/ })).toBeTruthy();
  });
});

describe("confirming", () => {
  it("says what will happen, then sends the choice, the trimmed reason and one key", async () => {
    const { w, confirm } = await toConfirm(wire());
    expect(
      within(confirm).getByText(
        "Zenith Polymers gets OpsGrids CRM on Growth, starting with a 21-day trial. Nothing is charged until the trial ends; the billing run then converts it.",
      ),
    ).toBeTruthy();
    expect(within(confirm).getByText(/within about 15 minutes/)).toBeTruthy();
    await userEvent.click(within(confirm).getByRole("button", { name: "Start trial" }));
    await waitFor(() => expect(w.addSpy).toHaveBeenCalledTimes(1));
    const [orgId, request, key] = w.addSpy.mock.calls[0] as [string, AddProductRequest, string];
    expect(orgId).toBe("org_test");
    expect(request).toEqual({
      product_key: "crm",
      plan_version_id: "pv_growth",
      trial: true,
      acknowledge_immediate_charge: false,
      reason: "Onboarding the sales team",
    });
    expect(key).toMatch(/^[0-9a-f-]{36}$/);
  });

  it("a double click sends exactly once", async () => {
    const { w, confirm } = await toConfirm(wire());
    let resolve: (value: AddProductResult) => void = () => undefined;
    w.addSpy.mockImplementation(() => new Promise<AddProductResult>((r) => (resolve = r)));
    await userEvent.dblClick(within(confirm).getByRole("button", { name: "Start trial" }));
    expect(w.addSpy).toHaveBeenCalledTimes(1);
    resolve(TRIAL_RESULT);
    await screen.findByText("Trial started");
    expect(w.addSpy).toHaveBeenCalledTimes(1);
  });

  it("announces the result politely, closes the dialogs, tells the page, and returns focus", async () => {
    wire();
    const onChanged = vi.fn();
    render(<AddProductCard org={ORG} onChanged={onChanged} />);
    const { dialog, trigger } = await openDialog();
    await fill(dialog);
    await userEvent.click(within(dialog).getByRole("button", { name: "Review" }));
    const confirm = await screen.findByRole("dialog", { name: "Confirm new subscription" });
    await userEvent.click(within(confirm).getByRole("button", { name: "Start trial" }));

    const region = await screen.findByRole("status");
    expect(region.getAttribute("aria-live")).toBe("polite");
    await waitFor(() => expect(within(region).getByText("Trial started")).toBeTruthy());
    expect(within(region).getByText(/trialling until 27 Oct 2026, 14:30 IST/)).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(onChanged).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(document.activeElement).toBe(trigger));
  });
});

describe("a failed confirm", () => {
  it.each([
    [409, "already_subscribed", "this organization already has a subscription for 'crm' (cancelled)", /cannot be reopened or replaced yet/],
    [422, "org_currency_mismatch", "this organization's subscriptions are billed in INR", /one currency/],
    [422, "one_time", "a one_time plan has no billing period", /never billed/],
    [422, "not_offerable", "that plan version is no longer on offer for this organization", /catalogue has moved on/],
  ] as const)("a %s %s returns to the form with the server's words, its reference and next step", async (status, code, detail, guidance) => {
    const { w, dialog } = await toConfirm(wire());
    w.addSpy.mockRejectedValue(problem(status, code, detail));
    await userEvent.click(within(screen.getByRole("dialog", { name: "Confirm new subscription" })).getByRole("button", { name: "Start trial" }));
    expect(await within(dialog).findByText(detail)).toBeTruthy();
    expect(within(dialog).getByText(/Reference: req-1234/)).toBeTruthy();
    expect(within(dialog).getByText(guidance)).toBeTruthy();
    expect(screen.queryByRole("dialog", { name: "Confirm new subscription" })).toBeNull();
    await waitFor(() => expect(w.getOptions).toHaveBeenCalledTimes(2));
  });

  it("mints a new key after a refusal", async () => {
    const { w, dialog } = await toConfirm(wire());
    w.addSpy.mockRejectedValueOnce(problem(422, "org_currency_mismatch", "billed in INR")).mockResolvedValueOnce(TRIAL_RESULT);
    await userEvent.click(within(screen.getByRole("dialog", { name: "Confirm new subscription" })).getByRole("button", { name: "Start trial" }));
    await within(dialog).findByText("billed in INR");
    await within(dialog).findByRole("combobox", { name: /Product/ });
    await userEvent.click(within(dialog).getByRole("button", { name: "Review" }));
    const second = await screen.findByRole("dialog", { name: "Confirm new subscription" });
    await userEvent.click(within(second).getByRole("button", { name: "Start trial" }));
    await waitFor(() => expect(w.addSpy).toHaveBeenCalledTimes(2));
    const keys = w.addSpy.mock.calls.map((c) => c[2]);
    expect(keys[0]).not.toBe(keys[1]);
  });

  it("a catalogue outage says nothing was changed, not that the outcome is unknown", async () => {
    const { w, confirm } = await toConfirm(wire());
    w.addSpy
      .mockRejectedValueOnce(problem(503, "catalog_unavailable", "could not check this plan against the catalogue"))
      .mockResolvedValueOnce(TRIAL_RESULT);
    await userEvent.click(within(confirm).getByRole("button", { name: "Start trial" }));
    expect(await within(confirm).findByText(/Nothing was changed\. Try again in a moment/)).toBeTruthy();
    expect(within(confirm).queryByText(/may or may not/)).toBeNull();
    await userEvent.click(within(confirm).getByRole("button", { name: "Try again" }));
    await screen.findByText("Trial started");
  });

  it.each([
    ["a 503", problem(503, "outcome_unknown", "subscription did not answer, so the change may or may not have been made.")],
    ["a dropped connection", new TypeError("Failed to fetch")],
  ])("%s keeps the confirm open, says the outcome is unknown, and Try again reuses the key", async (_label, failure) => {
    const { w, confirm } = await toConfirm(wire());
    w.addSpy.mockRejectedValueOnce(failure).mockResolvedValueOnce(TRIAL_RESULT);
    await userEvent.click(within(confirm).getByRole("button", { name: "Start trial" }));
    expect(await within(confirm).findByText(String((failure as Error).message))).toBeTruthy();
    expect(within(confirm).getByText(/Refresh this account to check/)).toBeTruthy();
    await userEvent.click(within(confirm).getByRole("button", { name: "Try again" }));
    await screen.findByText("Trial started");
    expect(w.addSpy.mock.calls[0]?.[2]).toBe(w.addSpy.mock.calls[1]?.[2]);
  });
});
