import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { MockInstance } from "vitest";

import * as client from "@/api/client";
import { ApiError } from "@/api/http";
import type { ExtendTrialRequest, ExtendTrialResult, OrganizationPricing, PricingOrgRef, ProductPricing } from "@/api/types";
import * as authContext from "@/auth/context";
import type { AuthContextValue } from "@/auth/context";
import type { OperatorRole } from "@/auth/types";
import { ExtendTrialCard } from "./AccountExtendTrial";

/**
 * Extend trial end to end (ADR-0031): owner only (billing_ops and support see a disabled button and
 * the hint), dates in IST with the zone named, the cap shown and enforced on the date field, the
 * absolute end and the end the operator saw sent exactly, the announced result, a double click
 * sending once, and every failure kind.
 */

const ORG: PricingOrgRef = { organization_id: "org_test", name: "Zenith Polymers" };
const NOW = new Date("2026-10-06T06:00:00Z");
/** The end as the live composite returns it — microseconds and all, which must go back verbatim. */
const TRIAL_END = "2026-10-20T09:00:00.123456Z";

function trialling(overrides: Partial<NonNullable<ProductPricing["subscription"]>> = {}): ProductPricing {
  return {
    product_key: "hrms",
    product_name: "OpsGrids HR",
    subscription: {
      id: "sub_1",
      state: "trial",
      plan_version_id: "pv_growth",
      plan_name: "Growth",
      trial_ends_at: TRIAL_END,
      current_period_end: "2026-11-05T09:00:00Z",
      pending_plan_version_id: null,
      pending_plan_name: null,
      cancel_at: null,
      entitled_products: ["hrms"],
      current_period_start: "2026-10-01T00:00:00Z",
      trial_max_ends_at: "2026-12-30T00:00:00Z",
      ...overrides,
    },
    current_price: null,
    public_price: null,
    history: [],
  };
}

function pricing(...products: ProductPricing[]): OrganizationPricing {
  return { organization_id: "org_test", products };
}

const RESULT: ExtendTrialResult = {
  subscription_id: "sub_1",
  product_key: "hrms",
  state: "trial",
  trial_ends_at: "2026-11-03T18:29:59Z",
  previous_trial_ends_at: TRIAL_END,
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
  extendSpy: MockInstance<typeof client.extendTrial>;
}

function wire(products: ProductPricing[] = [trialling()]): Wired {
  return {
    getPricing: vi.spyOn(client, "getOrganizationPricing").mockResolvedValue(pricing(...products)),
    extendSpy: vi.spyOn(client, "extendTrial").mockResolvedValue(RESULT),
  };
}

async function openDialog() {
  const trigger = await screen.findByRole("button", { name: "Extend trial for OpsGrids HR" });
  await userEvent.click(trigger);
  const dialog = await screen.findByRole("dialog", { name: /Extend trial — OpsGrids HR/ });
  return { trigger, dialog };
}

function setDate(dialog: HTMLElement, value: string) {
  // A native date input is set, not typed into, in jsdom.
  fireEvent.change(within(dialog).getByLabelText(/New last day of the trial/), { target: { value } });
}

async function toConfirm(w: Wired) {
  render(<ExtendTrialCard org={ORG} now={NOW} />);
  const { dialog, trigger } = await openDialog();
  setDate(dialog, "2026-11-03");
  await userEvent.type(within(dialog).getByRole("textbox", { name: /Reason/ }), "  Their data migration slipped  ");
  await userEvent.click(within(dialog).getByRole("button", { name: "Review" }));
  const confirm = await screen.findByRole("dialog", { name: "Confirm trial extension" });
  return { w, dialog, confirm, trigger };
}

beforeEach(() => {
  mockAuth(["owner"]);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("the card", () => {
  it("lists trialling subscriptions with their end and cap in IST, and only those", async () => {
    wire([trialling(), { ...trialling({ state: "active", trial_max_ends_at: null }), product_key: "crm", product_name: "OpsGrids CRM" }]);
    render(<ExtendTrialCard org={ORG} now={NOW} />);
    expect(await screen.findByText("OpsGrids HR")).toBeTruthy();
    expect(screen.queryByText("OpsGrids CRM")).toBeNull();
    expect(screen.getByText("20 Oct 2026, 14:30 IST")).toBeTruthy();
    expect(screen.getByText("30 Dec 2026, 05:30 IST")).toBeTruthy();
    expect(screen.getByText(/Dates are in IST \(UTC\+05:30\)/)).toBeTruthy();
  });

  it("says there is nothing to extend when nothing is trialling", async () => {
    wire([trialling({ state: "active", trial_max_ends_at: null })]);
    render(<ExtendTrialCard org={ORG} now={NOW} />);
    expect(await screen.findByText("No trials to extend")).toBeTruthy();
  });

  it("re-reads when a sibling says the account changed", async () => {
    const w = wire();
    const { rerender } = render(<ExtendTrialCard org={ORG} now={NOW} refreshSignal={0} />);
    await screen.findByText("OpsGrids HR");
    rerender(<ExtendTrialCard org={ORG} now={NOW} refreshSignal={1} />);
    await waitFor(() => expect(w.getPricing).toHaveBeenCalledTimes(2));
  });
});

describe("role hints", () => {
  it.each([["billing_ops"], ["support"]] as const)("disables the button for %s and says only an owner can", async (role) => {
    mockAuth([role]);
    wire();
    render(<ExtendTrialCard org={ORG} now={NOW} />);
    const button = (await screen.findByRole("button", { name: "Extend trial for OpsGrids HR" })) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(screen.getAllByText("Only an owner can extend a trial.").length).toBeGreaterThan(0);
  });

  it("disables it for a trial already at its cap, with the reason", async () => {
    wire([trialling({ trial_ends_at: "2026-12-29T18:29:59Z" })]);
    render(<ExtendTrialCard org={ORG} now={NOW} />);
    const button = (await screen.findByRole("button", { name: "Extend trial for OpsGrids HR" })) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(screen.getAllByText("This trial is already at its longest.").length).toBeGreaterThan(0);
  });
});

describe("the form", () => {
  it("names the zone, shows the cap, and bounds the date field to what the server would take", async () => {
    wire();
    render(<ExtendTrialCard org={ORG} now={NOW} />);
    const { dialog } = await openDialog();
    expect(within(dialog).getByText(/at most 90 days in total/)).toBeTruthy();
    expect(within(dialog).getByText("30 Dec 2026, 05:30 IST")).toBeTruthy();
    const input = within(dialog).getByLabelText(/New last day of the trial/) as HTMLInputElement;
    expect(input.type).toBe("date");
    expect([input.min, input.max]).toEqual(["2026-10-20", "2026-12-29"]);
    expect(within(dialog).getByText(/23:59 IST \(UTC\+05:30\)/)).toBeTruthy();
  });

  it("says the reason stays with staff, never on the customer org's event", async () => {
    wire();
    render(<ExtendTrialCard org={ORG} now={NOW} />);
    const { dialog } = await openDialog();
    expect(within(dialog).getByText("Recorded on the staff audit trail only. Not sent to the customer.")).toBeTruthy();
    expect(dialog.textContent).not.toMatch(/subscription's event/);
  });

  it("refuses to continue without a day and a reason, or with a day out of range, and calls nothing", async () => {
    const w = wire();
    render(<ExtendTrialCard org={ORG} now={NOW} />);
    const { dialog } = await openDialog();
    await userEvent.click(within(dialog).getByRole("button", { name: "Review" }));
    expect(await within(dialog).findByText("Choose the trial's new last day.")).toBeTruthy();
    expect(within(dialog).getByText("Enter a reason.")).toBeTruthy();
    setDate(dialog, "2027-01-15");
    expect(await within(dialog).findByText("Choose a day from 20 Oct 2026 to 29 Dec 2026.")).toBeTruthy();
    expect(w.extendSpy).not.toHaveBeenCalled();
  });
});

describe("confirming", () => {
  it("says both ends in IST, then sends the absolute end, the end it read verbatim and one key", async () => {
    const { w, confirm } = await toConfirm(wire());
    expect(
      within(confirm).getByText(
        "Zenith Polymers's OpsGrids HR trial will end at 03 Nov 2026, 23:59 IST instead of 20 Oct 2026, 14:30 IST. Nothing is charged before then; the billing run converts it at the new end.",
      ),
    ).toBeTruthy();
    await userEvent.click(within(confirm).getByRole("button", { name: "Extend trial" }));
    await waitFor(() => expect(w.extendSpy).toHaveBeenCalledTimes(1));
    const [orgId, productKey, request, key] = w.extendSpy.mock.calls[0] as [string, string, ExtendTrialRequest, string];
    expect([orgId, productKey]).toEqual(["org_test", "hrms"]);
    expect(request).toEqual({
      new_trial_ends_at: "2026-11-03T23:59:59+05:30",
      expected_trial_ends_at: TRIAL_END,
      reason: "Their data migration slipped",
    });
    expect(key).toMatch(/^[0-9a-f-]{36}$/);
  });

  it("a double click sends exactly once", async () => {
    const { w, confirm } = await toConfirm(wire());
    let resolve: (value: ExtendTrialResult) => void = () => undefined;
    w.extendSpy.mockImplementation(() => new Promise<ExtendTrialResult>((r) => (resolve = r)));
    await userEvent.dblClick(within(confirm).getByRole("button", { name: "Extend trial" }));
    expect(w.extendSpy).toHaveBeenCalledTimes(1);
    resolve(RESULT);
    await screen.findByText("Trial extended");
    expect(w.extendSpy).toHaveBeenCalledTimes(1);
  });

  it("announces the new end politely, reloads quietly, and returns focus to the control", async () => {
    const { w, confirm, trigger } = await toConfirm(wire());
    await userEvent.click(within(confirm).getByRole("button", { name: "Extend trial" }));
    const region = await screen.findByRole("status");
    expect(region.getAttribute("aria-live")).toBe("polite");
    await waitFor(() => expect(within(region).getByText("Trial extended")).toBeTruthy());
    expect(within(region).getByText(/trial now ends at 03 Nov 2026, 23:59 IST/)).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();
    await waitFor(() => expect(w.getPricing).toHaveBeenCalledTimes(2));
    expect(screen.getByRole("button", { name: "Extend trial for OpsGrids HR" })).toBe(trigger);
    await waitFor(() => expect(document.activeElement).toBe(trigger));
  });
});

describe("focus after an extension to the last day allowed", () => {
  it("moves to the result's Dismiss, since the opening control is about to be disabled", async () => {
    const last = "2026-12-29T18:29:59Z";
    const w = wire();
    w.extendSpy.mockResolvedValue({ ...RESULT, trial_ends_at: last });
    render(<ExtendTrialCard org={ORG} now={NOW} />);
    const { dialog, trigger } = await openDialog();
    setDate(dialog, "2026-12-29");
    await userEvent.type(within(dialog).getByRole("textbox", { name: /Reason/ }), "As far as it can go");
    w.getPricing.mockResolvedValue(pricing(trialling({ trial_ends_at: last })));
    await userEvent.click(within(dialog).getByRole("button", { name: "Review" }));
    const confirm = await screen.findByRole("dialog", { name: "Confirm trial extension" });
    await userEvent.click(within(confirm).getByRole("button", { name: "Extend trial" }));
    const region = await screen.findByRole("status");
    await waitFor(() => expect(within(region).getByText("Trial extended")).toBeTruthy());
    await waitFor(() => expect((trigger as HTMLButtonElement).disabled).toBe(true));
    const dismiss = within(region).getByRole("button", { name: "Dismiss" });
    await waitFor(() => expect(document.activeElement).toBe(dismiss));
  });
});

describe("a failed confirm", () => {
  it.each([
    [409, "state_changed", "this subscription's trial end changed since you looked at it", /not the way it was/],
    [422, "trial_cap", "a trial may run at most 90 days in total", /longest a trial may run/],
    [422, "claim_in_flight", "billing has already claimed this trial's conversion", /already converting/],
    [422, "not_trial", "a active subscription is not trialling", /still trialling/],
  ] as const)("a %s %s returns to the form with the server's words and re-reads", async (status, code, detail, guidance) => {
    const { w, dialog } = await toConfirm(wire());
    w.extendSpy.mockRejectedValue(problem(status, code, detail));
    await userEvent.click(within(screen.getByRole("dialog", { name: "Confirm trial extension" })).getByRole("button", { name: "Extend trial" }));
    expect(await within(dialog).findByText(detail)).toBeTruthy();
    expect(within(dialog).getByText(/Reference: req-1234/)).toBeTruthy();
    expect(within(dialog).getByText(guidance)).toBeTruthy();
    expect(screen.queryByRole("dialog", { name: "Confirm trial extension" })).toBeNull();
    await waitFor(() => expect(w.getPricing.mock.calls.length).toBeGreaterThanOrEqual(2));
  });

  it("an unknown outcome keeps the confirm open, and Try again repeats the same request under the same key", async () => {
    const { w, confirm } = await toConfirm(wire());
    // The re-read after the unknown outcome shows the trial moved (it may be this very extension
    // landing): Try again must still send exactly what the key was minted for, or the server would
    // see a different body under the same key.
    w.getPricing.mockResolvedValue(pricing(trialling({ trial_ends_at: "2026-11-03T18:29:59Z" })));
    w.extendSpy
      .mockRejectedValueOnce(problem(503, "outcome_unknown", "subscription did not answer, so the change may or may not have been made."))
      .mockResolvedValueOnce(RESULT);
    await userEvent.click(within(confirm).getByRole("button", { name: "Extend trial" }));
    expect(
      await within(confirm).findByText("subscription did not answer, so the change may or may not have been made."),
    ).toBeTruthy();
    expect(within(confirm).getByText(/Refresh this account to check/)).toBeTruthy();
    await waitFor(() => expect(w.getPricing).toHaveBeenCalledTimes(2));
    expect((await screen.findAllByText("03 Nov 2026, 23:59 IST")).length).toBeGreaterThan(0);
    await userEvent.click(within(confirm).getByRole("button", { name: "Try again" }));
    await screen.findByText("Trial extended");
    expect(w.extendSpy.mock.calls[0]?.[3]).toBe(w.extendSpy.mock.calls[1]?.[3]);
    expect(w.extendSpy.mock.calls[1]?.[2]).toEqual(w.extendSpy.mock.calls[0]?.[2]);
    expect((w.extendSpy.mock.calls[1]?.[2] as ExtendTrialRequest).expected_trial_ends_at).toBe(TRIAL_END);
  });

  it("after a state_changed refusal the form shows the re-read trial, and the next confirm sends what it read", async () => {
    const moved = "2026-10-25T09:00:00.654321Z";
    const { w, dialog } = await toConfirm(wire());
    w.getPricing.mockResolvedValue(pricing(trialling({ trial_ends_at: moved })));
    w.extendSpy.mockRejectedValueOnce(problem(409, "state_changed", "this subscription's trial end changed since you looked at it"));
    await userEvent.click(within(screen.getByRole("dialog", { name: "Confirm trial extension" })).getByRole("button", { name: "Extend trial" }));
    expect(await within(dialog).findByText("this subscription's trial end changed since you looked at it")).toBeTruthy();

    // The open form follows the re-read: the new end, and a window that starts after it.
    await waitFor(() => expect(within(dialog).getByText("25 Oct 2026, 14:30 IST")).toBeTruthy());
    const input = within(dialog).getByLabelText(/New last day of the trial/) as HTMLInputElement;
    expect(input.min).toBe("2026-10-25");

    await userEvent.click(within(dialog).getByRole("button", { name: "Review" }));
    const confirm = await screen.findByRole("dialog", { name: "Confirm trial extension" });
    expect(within(confirm).getByText(/instead of 25 Oct 2026, 14:30 IST/)).toBeTruthy();
    await userEvent.click(within(confirm).getByRole("button", { name: "Extend trial" }));
    await screen.findByText("Trial extended");
    expect(w.extendSpy).toHaveBeenCalledTimes(2);
    const second = w.extendSpy.mock.calls[1] as [string, string, ExtendTrialRequest, string];
    expect(second[2].expected_trial_ends_at).toBe(moved);
    expect(second[3]).not.toBe(w.extendSpy.mock.calls[0]?.[3]);
  });

  it("a trial that is no longer trialling on the re-read says so, and cannot be confirmed again", async () => {
    const { w, dialog } = await toConfirm(wire());
    w.getPricing.mockResolvedValue(pricing(trialling({ state: "active", trial_ends_at: null, trial_max_ends_at: null })));
    w.extendSpy.mockRejectedValueOnce(problem(422, "not_trial", "a active subscription is not trialling"));
    await userEvent.click(within(screen.getByRole("dialog", { name: "Confirm trial extension" })).getByRole("button", { name: "Extend trial" }));
    expect(await within(dialog).findByText("a active subscription is not trialling")).toBeTruthy();
    expect(await within(dialog).findByText(/no longer on a trial/)).toBeTruthy();
    await userEvent.click(within(dialog).getByRole("button", { name: "Review" }));
    expect(screen.queryByRole("dialog", { name: "Confirm trial extension" })).toBeNull();
    expect(w.extendSpy).toHaveBeenCalledTimes(1);
  });
});
