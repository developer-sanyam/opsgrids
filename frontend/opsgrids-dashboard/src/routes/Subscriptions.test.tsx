import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { ApiError, PermissionProvider } from "@opsgrids/client";

import type { PlanChangePreview, PlanOption, PlanOptions, PlanRef, SubscriptionPlanSummary } from "@/api/types";
import { formatDate } from "@/lib/money";

import { SubscriptionsPage } from "./Subscriptions";

vi.mock("@/api", () => ({
  listSubscriptions: vi.fn(),
  listProducts: vi.fn(),
  listSubscriptionPlanSummaries: vi.fn(),
  getSubscriptionPlanOptions: vi.fn(),
  previewSubscriptionPlanChange: vi.fn(),
  cancelSubscription: vi.fn(),
  changeSubscriptionPlan: vi.fn(),
}));

vi.mock("@/lib/csv", () => ({ downloadCsv: vi.fn() }));

import * as api from "@/api";
import { downloadCsv } from "@/lib/csv";

const base = {
  plan_version_id: "v",
  state: "active",
  trial_ends_at: null,
  current_period_start: null,
  current_period_end: null,
  cancel_at: null,
  currency: "INR",
  items: [],
  dunning: null,
};

const products = [
  { key: "suite", name: "OpsGrids Suite", is_bundle: true },
  { key: "hrms", name: "OpsGrids People", is_bundle: false },
  { key: "crm", name: "OpsGrids Revenue", is_bundle: false },
  { key: "finance", name: "OpsGrids Finance", is_bundle: false },
];

describe("SubscriptionsPage — bundles", () => {
  it("a bundle lists what it includes, and a standalone product says the bundle also covers it", async () => {
    vi.mocked(api.listProducts).mockResolvedValue(products as never);
    vi.mocked(api.listSubscriptions).mockResolvedValue([
      { ...base, id: "s1", product_key: "hrms", entitled_products: ["hrms"] },
      { ...base, id: "s2", product_key: "suite", entitled_products: ["hrms", "crm", "finance"] },
    ] as never);

    render(
      <PermissionProvider grants={["core.product.activation.read:ORG"]}>
        <SubscriptionsPage />
      </PermissionProvider>,
    );

    expect(await screen.findByText("Includes People, Revenue and Finance")).toBeTruthy();
    expect(screen.getByText("Also included in Suite")).toBeTruthy();
  });
});

// ------------------------------------------------------------------------------------ fixtures

const OWNER = ["core.product.activation.write:ORG", "core.product.activation.read:ORG", "core.billing.invoice.read:ORG"];
const PERIOD_END = "2026-11-04T00:00:00Z";
const DAY = 86_400_000;

/** The owner's org on 2026-10-06: Revenue's trial converted to active by the billing run. */
const revenue = {
  ...base,
  id: "s1",
  product_key: "crm",
  plan_version_id: "growth-1",
  state: "active",
  trial_ends_at: "2026-10-05T00:00:00Z",
  current_period_start: "2026-10-05T00:00:00Z",
  current_period_end: PERIOD_END,
  pending_plan_version_id: null,
  entitled_products: ["crm"],
};

function ref(overrides: Partial<PlanRef>): PlanRef {
  return {
    plan_version_id: "growth-1",
    plan_id: "growth",
    plan_name: "Revenue Growth",
    version_no: 1,
    billing_interval: "month",
    negotiated: false,
    amount_visible: false,
    currency: null,
    recurring_minor: null,
    prices: null,
    ...overrides,
  };
}

function option(overrides: Partial<PlanOption>): PlanOption {
  return { ...ref({}), is_current: false, is_pending: false, blocked_code: null, blocked_reason: null, ...overrides };
}

/** What was live: "Revenue Growth" twice beside "Revenue", plus a duplicate and a yearly plan. */
const OPTIONS: PlanOptions = {
  subscription_id: "s1",
  product_key: "crm",
  state: "active",
  change_blocked_code: null,
  current: ref({}),
  pending: null,
  pending_effective_at: null,
  pending_scheduled_by: null,
  options: [
    option({ is_current: true, blocked_code: "current", blocked_reason: "This is the plan you are on." }),
    option({ plan_version_id: "growth-2", version_no: 2 }),
    option({
      plan_version_id: "growth-3",
      version_no: 3,
      negotiated: true,
      amount_visible: true,
      currency: "INR",
      recurring_minor: 180000,
      prices: [{ kind: "flat", amount_minor: 180000, unit: null, unit_price_micros: null, meter_key: null }],
    }),
    option({ plan_version_id: "rev-1", plan_id: "rev", plan_name: "Revenue" }),
    option({ plan_version_id: "rev-1", plan_id: "rev", plan_name: "Revenue" }),
    option({
      plan_version_id: "rev-year",
      plan_id: "rev",
      plan_name: "Revenue Annual",
      billing_interval: "year",
      blocked_code: "cadence",
      blocked_reason: "Billed yearly; this subscription renews monthly.",
    }),
  ],
};

function preview(overrides: Partial<PlanChangePreview>): PlanChangePreview {
  return {
    expected: {
      state: "active",
      plan_version_id: "growth-1",
      pending_plan_version_id: null,
      current_period_end: PERIOD_END,
      lifecycle_claim_id: null,
    },
    action: "switch",
    direction: "downgrade",
    effective: "next_period",
    effective_at: PERIOD_END,
    would_clear_pending: false,
    entitled_added: [],
    entitled_removed: [],
    usage_priced: false,
    usage_closed_now: false,
    paid: true,
    prorated_charge_now: false,
    refusal_code: null,
    refusal_detail: null,
    ...overrides,
  };
}

function summary(overrides: Partial<SubscriptionPlanSummary> = {}): SubscriptionPlanSummary {
  return {
    subscription_id: "s1",
    current: ref({}),
    pending: null,
    pending_effective_at: null,
    pending_scheduled_by: null,
    ...overrides,
  };
}

function setup({
  subscriptions = [revenue],
  summaries = [summary()],
  grants = OWNER,
  options = OPTIONS,
}: {
  subscriptions?: object[];
  summaries?: SubscriptionPlanSummary[];
  grants?: string[];
  options?: PlanOptions;
} = {}) {
  vi.clearAllMocks();
  vi.mocked(api.listProducts).mockResolvedValue(products as never);
  vi.mocked(api.listSubscriptions).mockResolvedValue(subscriptions as never);
  vi.mocked(api.listSubscriptionPlanSummaries).mockResolvedValue(summaries);
  vi.mocked(api.getSubscriptionPlanOptions).mockResolvedValue(options);
  // The page sits under the app's router, and a past-due row can render a router Link.
  render(
    <MemoryRouter>
      <PermissionProvider grants={grants}>
        <SubscriptionsPage />
      </PermissionProvider>
    </MemoryRouter>,
  );
  return userEvent.setup();
}

async function openChangePlan(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole("button", { name: "Change plan Revenue" }));
  return screen.findByRole("dialog", { name: "Change Revenue's plan" });
}

// ------------------------------------------------------------------------------------ the table

describe("SubscriptionsPage — what each row says", () => {
  it("an active subscription renews; the converted trial's end date is gone", async () => {
    setup();
    expect(await screen.findByText(`Renews ${formatDate(PERIOD_END)}`)).toBeTruthy();
    expect(screen.queryByRole("columnheader", { name: "Trial ends" })).toBeNull();
    expect(screen.getByRole("columnheader", { name: "Renewal" })).toBeTruthy();
    expect(screen.queryByText(/\(0 days\)/)).toBeNull();
    expect(screen.queryByText(/Trial ends/)).toBeNull();
  });

  it("a trial says when it ends and how many days are left; past_due and suspended are stated in words", async () => {
    const endsAt = new Date(Date.now() + 10 * DAY - 60_000).toISOString();
    setup({
      subscriptions: [
        { ...revenue, state: "trial", trial_ends_at: endsAt },
        { ...base, id: "s2", product_key: "finance", state: "past_due", entitled_products: ["finance"] },
        { ...base, id: "s3", product_key: "hrms", state: "suspended", entitled_products: ["hrms"] },
      ],
      summaries: [],
    });
    expect(await screen.findByText(`Trial ends ${formatDate(endsAt)}`)).toBeTruthy();
    expect(screen.getByText("10 days left")).toBeTruthy();
    expect(screen.getByText("Awaiting payment")).toBeTruthy();
    expect(screen.getByText("Access suspended")).toBeTruthy();
  });

  it("says 'Payment overdue' once per row: the dunning line has it, the renewal column does not repeat it", async () => {
    setup({
      subscriptions: [
        {
          ...revenue,
          state: "past_due",
          dunning: {
            cycle_id: "cy1",
            opened_at: "2026-10-01T00:00:00Z",
            grace_ends_at: "2026-10-15T00:00:00Z",
            next_attempt_at: null,
            attempts_made: 1,
            max_attempts: 4,
            amount_minor: 499900,
            currency: "INR",
          },
        },
      ],
    });
    expect(await screen.findByText(/Payment overdue/)).toBeTruthy();
    expect(screen.getAllByText(/Payment overdue/)).toHaveLength(1);
    expect(screen.getByText("Awaiting payment")).toBeTruthy();
  });

  it("shows the plan, its cadence, and a scheduled change — never a list price", async () => {
    setup({
      subscriptions: [{ ...revenue, pending_plan_version_id: "rev-1" }],
      summaries: [
        summary({
          pending: ref({ plan_version_id: "rev-1", plan_name: "Revenue" }),
          pending_effective_at: PERIOD_END,
        }),
      ],
    });
    expect(await screen.findByText("Revenue Growth")).toBeTruthy();
    expect(screen.getByText("Billed monthly")).toBeTruthy();
    expect(screen.getByText(`Changes to Revenue on ${formatDate(PERIOD_END)}`)).toBeTruthy();
    expect(screen.queryByText(/₹/)).toBeNull();
  });

  it("shows the organisation's own negotiated price where the server sent one", async () => {
    setup({
      summaries: [
        summary({
          current: ref({
            negotiated: true,
            amount_visible: true,
            currency: "INR",
            recurring_minor: 180000,
            prices: [{ kind: "flat", amount_minor: 180000, unit: null, unit_price_micros: null, meter_key: null }],
          }),
        }),
      ],
    });
    expect(await screen.findByText("₹1,800.00 per month · agreed for your organisation")).toBeTruthy();
  });

  it("renders without the plan line when that read fails — and says so, with a retry", async () => {
    vi.clearAllMocks();
    vi.mocked(api.listProducts).mockResolvedValue(products as never);
    vi.mocked(api.listSubscriptions).mockResolvedValue([{ ...revenue, pending_plan_version_id: "rev-1" }] as never);
    vi.mocked(api.listSubscriptionPlanSummaries).mockRejectedValue(new Error("catalog down"));
    const user = userEvent.setup();
    render(
      <PermissionProvider grants={OWNER}>
        <SubscriptionsPage />
      </PermissionProvider>,
    );
    expect(await screen.findByText(`Renews ${formatDate(PERIOD_END)}`)).toBeTruthy();
    expect(screen.queryByText("Revenue Growth")).toBeNull();
    expect(screen.getByText("Plan details couldn't be loaded")).toBeTruthy();
    // The subscription row itself still knows a change is waiting.
    expect(screen.getByText("A plan change is scheduled")).toBeTruthy();

    vi.mocked(api.listSubscriptionPlanSummaries).mockResolvedValue([summary()]);
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("Revenue Growth")).toBeTruthy();
    expect(screen.queryByText("Plan details couldn't be loaded")).toBeNull();
  });

  it("a cancelled row shows no scheduled change, on screen or in the export", async () => {
    const cancelled = { ...revenue, state: "cancelled", cancel_at: "2026-10-06T00:00:00Z", pending_plan_version_id: "rev-1" };
    const user = setup({
      subscriptions: [cancelled],
      summaries: [
        summary({ pending: ref({ plan_version_id: "rev-1", plan_name: "Revenue" }), pending_effective_at: PERIOD_END }),
      ],
    });
    expect(await screen.findByText(`Cancelled ${formatDate("2026-10-06T00:00:00Z")}`)).toBeTruthy();
    expect(screen.queryByText(/Changes to/)).toBeNull();
    expect(screen.queryByText("A plan change is scheduled")).toBeNull();

    await user.click(screen.getByRole("checkbox", { name: /Revenue/ }));
    await user.click(screen.getByRole("button", { name: "Export selected" }));
    expect(downloadCsv).toHaveBeenCalledTimes(1);
    const [, header, rows] = vi.mocked(downloadCsv).mock.calls[0] ?? [];
    const at = (name: string) => (header ?? []).indexOf(name);
    expect(rows?.[0]?.[at("Scheduled plan")]).toBe("");
    expect(rows?.[0]?.[at("Scheduled from")]).toBe("");
  });

  it("a change OpsGrids scheduled is named as agreed, and only OpsGrids can undo it", async () => {
    setup({
      subscriptions: [{ ...revenue, pending_plan_version_id: "growth-agreed" }],
      summaries: [
        summary({
          pending: ref({ plan_version_id: "growth-agreed", plan_name: "Revenue Growth (agreed)" }),
          pending_effective_at: PERIOD_END,
          pending_scheduled_by: "opsgrids",
        }),
      ],
    });
    expect(
      await screen.findByText(`Changes to Revenue Growth (agreed) on ${formatDate(PERIOD_END)} · agreed with OpsGrids`),
    ).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Keep current plan/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /More actions/ })).toBeNull();
    const change = screen.getByRole("button", { name: "Change plan Revenue" });
    expect(change.getAttribute("aria-disabled")).toBe("true");
    expect(
      screen.getByText("A plan change agreed with OpsGrids is scheduled. Contact info@opsgrids.com to change it."),
    ).toBeTruthy();
  });

  it("an active subscription past its period end says the renewal is due, not 'Renews' a past date", async () => {
    const ended = new Date(Date.now() - 2 * DAY).toISOString();
    setup({ subscriptions: [{ ...revenue, current_period_end: ended }] });
    expect(await screen.findByText(`Renewal due ${formatDate(ended)}`)).toBeTruthy();
    expect(screen.queryByText(/^Renews/)).toBeNull();
  });

  it("describes the page as plans and renewals, not only trials", async () => {
    setup();
    expect(
      await screen.findByText(
        "Each product your organisation has started: its plan, its state, and when it renews or its trial ends.",
      ),
    ).toBeTruthy();
  });

  it("offers no change or cancel to a member who may only read, and explains an unavailable change", async () => {
    setup({ grants: ["core.product.activation.read:ORG"] });
    await screen.findByText("Revenue Growth");
    expect(screen.queryByRole("button", { name: /Change plan/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Cancel/ })).toBeNull();
  });

  it("keeps Change plan visible but unavailable, with the reason, on a past-due subscription", async () => {
    setup({ subscriptions: [{ ...revenue, state: "past_due" }] });
    const button = await screen.findByRole("button", { name: "Change plan Revenue" });
    expect(button.getAttribute("aria-disabled")).toBe("true");
    expect(screen.getByText("Pay the overdue invoice before changing plan.")).toBeTruthy();
  });

  it("at phone width, each subscription is a card with its plan and renewal", async () => {
    vi.stubGlobal("matchMedia", (query: string) => ({
      matches: /min-width:\s*(\d+)px/.exec(query)?.[1] !== undefined ? 360 >= Number(/min-width:\s*(\d+)px/.exec(query)?.[1]) : false,
      media: query,
      onchange: null,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      addListener: () => undefined,
      removeListener: () => undefined,
      dispatchEvent: () => false,
    }));
    try {
      setup();
      expect(await screen.findByText(`Renews ${formatDate(PERIOD_END)}`)).toBeTruthy();
      expect(screen.queryByRole("table")).toBeNull();
      expect(screen.getByText("Revenue Growth")).toBeTruthy();
      expect(screen.getByRole("button", { name: "Change plan Revenue" })).toBeTruthy();
    } finally {
      vi.unstubAllGlobals();
    }
  });
});

// ------------------------------------------------------------------------------------ the dialog

describe("SubscriptionsPage — change plan picker", () => {
  it("lists each plan once, marks and disables the current one, and tells same-named plans apart", async () => {
    const user = setup();
    const dialog = await openChangePlan(user);
    const radios = await within(dialog).findAllByRole("radio");
    const names = radios.map((r) => r.closest("label")?.textContent ?? "");
    expect(names).toHaveLength(5);
    expect(names.filter((n) => n.startsWith("Revenue") && !n.startsWith("Revenue Growth") && !n.startsWith("Revenue Annual"))).toHaveLength(1);
    expect(names[0]).toMatch(/^Revenue Growth · v1Current plan/);
    expect((radios[0] as HTMLInputElement).disabled).toBe(true);
    expect(names[1]).toMatch(/^Revenue Growth · v2/);
    expect(names[2]).toMatch(/^Revenue Growth · v3Negotiated₹1,800\.00 per month · agreed for your organisation$/);
    expect(names[4]).toMatch(/Billed yearly; this subscription renews monthly\./);
    expect((radios[4] as HTMLInputElement).disabled).toBe(true);
    expect(within(dialog).getByRole("group", { name: "Move to" })).toBeTruthy();
  });

  it("never promises 'immediately': the confirm says what the server's preview says", async () => {
    const user = setup();
    vi.mocked(api.previewSubscriptionPlanChange).mockResolvedValue(preview({}));
    vi.mocked(api.changeSubscriptionPlan).mockResolvedValue({ ...revenue, pending_plan_version_id: "rev-1" } as never);
    const dialog = await openChangePlan(user);
    expect(within(dialog).queryByText(/immediately/i)).toBeNull();
    await user.click(await within(dialog).findByRole("radio", { name: /^Revenue Billed monthly/ }));
    await user.click(within(dialog).getByRole("button", { name: "Review change" }));

    const confirm = await screen.findByRole("dialog", { name: "Schedule Revenue's move to Revenue?" });
    expect(api.previewSubscriptionPlanChange).toHaveBeenCalledWith("s1", "rev-1");
    expect(
      within(confirm).getByText(
        `Revenue stays on Revenue Growth until ${formatDate(PERIOD_END)}, then moves to Revenue (a lower price). Nothing changes before then, and there is no refund or credit for the rest of the current period.`,
      ),
    ).toBeTruthy();
    expect(screen.queryByText(/immediately/i)).toBeNull();

    await user.click(within(confirm).getByRole("button", { name: "Schedule change" }));
    await waitFor(() =>
      expect(api.changeSubscriptionPlan).toHaveBeenCalledWith("s1", "rev-1", preview({}).expected),
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("a paid upgrade says it moves now and the difference is invoiced", async () => {
    const user = setup();
    vi.mocked(api.previewSubscriptionPlanChange).mockResolvedValue(
      preview({ direction: "upgrade", effective: "now", effective_at: null, prorated_charge_now: true }),
    );
    const dialog = await openChangePlan(user);
    await user.click(await within(dialog).findByRole("radio", { name: /^Revenue Growth · v2/ }));
    await user.click(within(dialog).getByRole("button", { name: "Review change" }));
    const confirm = await screen.findByRole("dialog", { name: "Move Revenue to Revenue Growth · v2 now?" });
    expect(within(confirm).getByText(/invoiced separately/)).toBeTruthy();
    expect(within(confirm).getByText("Product access updates within about 15 minutes.")).toBeTruthy();
    expect(within(confirm).getByRole("button", { name: "Change plan" })).toBeTruthy();
  });

  it("a refusal is the answer: the next step, announced in the dialog's alert, and no confirm", async () => {
    const user = setup();
    vi.mocked(api.previewSubscriptionPlanChange).mockResolvedValue(
      preview({
        direction: null,
        effective: null,
        effective_at: null,
        refusal_code: "billing_catching_up",
        refusal_detail: "a billing period on this subscription is due and has not been processed yet",
      }),
    );
    const dialog = await openChangePlan(user);
    await user.click(await within(dialog).findByRole("radio", { name: /^Revenue Billed monthly/ }));
    await user.click(within(dialog).getByRole("button", { name: "Review change" }));
    const alert = await within(dialog).findByRole("alert");
    expect(alert.textContent).toMatch(/still processing this subscription's latest billing period/);
    expect(alert.textContent).toMatch(/contact info@opsgrids\.com/);
    // The server's sentence is for the record, not the headline.
    expect(within(dialog).queryByText(/has not been processed yet/)).toBeNull();
    expect(screen.queryByRole("button", { name: "Schedule change" })).toBeNull();
  });

  it("Review change while the plans are still loading says so instead of doing nothing", async () => {
    const user = setup();
    vi.mocked(api.getSubscriptionPlanOptions).mockReturnValue(new Promise(() => undefined));
    const dialog = await openChangePlan(user);
    await user.click(within(dialog).getByRole("button", { name: "Review change" }));
    expect((await within(dialog).findByRole("alert")).textContent).toMatch(/still loading/);
    expect(api.previewSubscriptionPlanChange).not.toHaveBeenCalled();
  });

  it("the scheduled plan is listed but cannot be picked again", async () => {
    const user = setup({
      options: {
        ...OPTIONS,
        pending: ref({ plan_version_id: "rev-1", plan_name: "Revenue" }),
        pending_effective_at: PERIOD_END,
        pending_scheduled_by: "customer",
        options: [
          OPTIONS.options[0] as PlanOption,
          option({
            plan_version_id: "rev-1",
            plan_id: "rev",
            plan_name: "Revenue",
            is_pending: true,
            blocked_code: "pending",
            blocked_reason: "Already scheduled. To stay on your current plan instead, use Keep current plan.",
          }),
          option({ plan_version_id: "growth-2", version_no: 2 }),
        ],
      },
    });
    const dialog = await openChangePlan(user);
    const scheduled = await within(dialog).findByRole("radio", { name: /^Revenue\b.*Already scheduled\./ });
    expect((scheduled as HTMLInputElement).disabled).toBe(true);
    expect(within(dialog).getByText("Scheduled")).toBeTruthy();
    expect(within(dialog).getByText(/Choosing another plan replaces it\./)).toBeTruthy();
  });

  it("while OpsGrids' scheduled change waits, nothing can be picked and nothing says it would be replaced", async () => {
    const user = setup({
      options: {
        ...OPTIONS,
        change_blocked_code: "scheduled_by_opsgrids",
        pending: ref({ plan_version_id: "growth-agreed", plan_name: "Revenue Growth (agreed)" }),
        pending_effective_at: PERIOD_END,
        pending_scheduled_by: "opsgrids",
      },
    });
    // The row's own Change plan is disabled in this state; open the dialog the way a stale page
    // would (the row did not know yet) to prove the dialog holds the line on its own.
    const dialog = await openChangePlan(user);
    const radios = await within(dialog).findAllByRole("radio");
    expect(radios.every((r) => (r as HTMLInputElement).disabled)).toBe(true);
    expect(within(dialog).getByText(/agreed with OpsGrids, is scheduled/)).toBeTruthy();
    expect(within(dialog).queryByText(/replaces it/)).toBeNull();
    expect(within(dialog).getByText(/Contact info@opsgrids\.com to change or cancel it/)).toBeTruthy();
  });

  it("tells a member who cannot read invoices why no price is shown", async () => {
    const user = setup({ grants: ["core.product.activation.write:ORG", "core.product.activation.read:ORG"] });
    const dialog = await openChangePlan(user);
    await within(dialog).findAllByRole("radio");
    expect(within(dialog).getByText(/Prices are shown only to people who can read your organisation's invoices/)).toBeTruthy();
    expect(within(dialog).queryByText(/Prices are shown only for plans agreed/)).toBeNull();
  });

  it("asks for a choice before asking the server", async () => {
    const user = setup();
    const dialog = await openChangePlan(user);
    await within(dialog).findAllByRole("radio");
    await user.click(within(dialog).getByRole("button", { name: "Review change" }));
    expect(within(dialog).getByText("Choose the plan to move to.")).toBeTruthy();
    expect(api.previewSubscriptionPlanChange).not.toHaveBeenCalled();
  });

  it("a stale confirm goes back to the picker with fresh options", async () => {
    const user = setup();
    vi.mocked(api.previewSubscriptionPlanChange).mockResolvedValue(preview({}));
    vi.mocked(api.changeSubscriptionPlan).mockRejectedValue(
      new ApiError(409, "this subscription changed since you looked at it; refresh and review it again", {
        status: 409,
        detail: "this subscription changed since you looked at it; refresh and review it again",
        errors: [{ field: "", code: "state_changed", message: "changed" }],
      }),
    );
    const dialog = await openChangePlan(user);
    await user.click(await within(dialog).findByRole("radio", { name: /^Revenue Billed monthly/ }));
    await user.click(within(dialog).getByRole("button", { name: "Review change" }));
    const confirm = await screen.findByRole("dialog", { name: /^Schedule/ });
    await user.click(within(confirm).getByRole("button", { name: "Schedule change" }));
    expect(await screen.findByText("This subscription changed since you opened this. Review the plans again.")).toBeTruthy();
    expect(screen.queryByRole("dialog", { name: /^Schedule/ })).toBeNull();
    await waitFor(() => expect(api.getSubscriptionPlanOptions).toHaveBeenCalledTimes(2));
  });
});

describe("SubscriptionsPage — keep current plan and cancel", () => {
  it("withdraws a scheduled change through the change-plan API, after asking the server", async () => {
    const user = setup({
      subscriptions: [{ ...revenue, pending_plan_version_id: "rev-1" }],
      summaries: [
        summary({ pending: ref({ plan_version_id: "rev-1", plan_name: "Revenue" }), pending_effective_at: PERIOD_END }),
      ],
    });
    const withdraw = preview({ action: "withdraw_scheduled", direction: null, effective: "now", effective_at: null });
    vi.mocked(api.previewSubscriptionPlanChange).mockResolvedValue(withdraw);
    vi.mocked(api.changeSubscriptionPlan).mockResolvedValue(revenue as never);

    // Three actions on this row: Keep current plan and Cancel sit behind "More actions" so the
    // pills never wrap onto two lines.
    await user.click(await screen.findByRole("button", { name: "More actions for Revenue" }));
    await user.click(screen.getByRole("menuitem", { name: "Keep current plan" }));
    const dialog = await screen.findByRole("dialog", { name: "Keep Revenue on Revenue Growth?" });
    expect(
      within(dialog).getByText(
        `The change to Revenue scheduled on ${formatDate(PERIOD_END)} is cancelled. Revenue stays on Revenue Growth, and nothing is charged.`,
      ),
    ).toBeTruthy();
    await user.click(within(dialog).getByRole("button", { name: "Keep current plan" }));
    await waitFor(() => expect(api.changeSubscriptionPlan).toHaveBeenCalledWith("s1", "growth-1", withdraw.expected));
    expect(api.previewSubscriptionPlanChange).toHaveBeenCalledWith("s1", "growth-1");
  });

  it("Keep current plan on a row that is behind closes, says so and reads the rows again", async () => {
    const user = setup({
      subscriptions: [{ ...revenue, pending_plan_version_id: "rev-1" }],
      summaries: [
        summary({ pending: ref({ plan_version_id: "rev-1", plan_name: "Revenue" }), pending_effective_at: PERIOD_END }),
      ],
    });
    // The billing run applied the change since the page loaded: the plan in force is no longer
    // offered, so the server answers `not_offerable` — there is no list here to choose again from.
    vi.mocked(api.previewSubscriptionPlanChange).mockResolvedValue(
      preview({ direction: null, effective: null, effective_at: null, refusal_code: "not_offerable", refusal_detail: "x" }),
    );
    await user.click(await screen.findByRole("button", { name: "More actions for Revenue" }));
    await user.click(screen.getByRole("menuitem", { name: "Keep current plan" }));
    const dialog = await screen.findByRole("dialog", { name: "Keep Revenue on Revenue Growth?" });
    await user.click(within(dialog).getByRole("button", { name: "Keep current plan" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await waitFor(() => expect(api.listSubscriptions).toHaveBeenCalledTimes(2));
    expect(api.changeSubscriptionPlan).not.toHaveBeenCalled();
    expect(screen.queryByText(/choose again/)).toBeNull();
  });

  it("offers Keep current plan only while a change is scheduled", async () => {
    setup();
    await screen.findByRole("button", { name: "Change plan Revenue" });
    expect(screen.queryByRole("button", { name: /Keep current plan/ })).toBeNull();
  });

  it("the cancel confirm says cancellation is immediate and final", async () => {
    const user = setup();
    await user.click(await screen.findByRole("button", { name: "Cancel Revenue" }));
    const dialog = await screen.findByRole("dialog", { name: "Cancel Revenue?" });
    expect(within(dialog).getByText(/Cancellation is immediate and final: access to Revenue ends now/)).toBeTruthy();
    expect(within(dialog).getByText(/can't be started again from this dashboard/)).toBeTruthy();
  });
});

describe("SubscriptionsPage — change plan", () => {
  it("offers plans from the server's offerable list, with no catalogue price shown anywhere", async () => {
    const user = setup();
    const dialog = await openChangePlan(user);
    await within(dialog).findAllByRole("radio");
    expect(within(dialog).queryByText(/₹4,999\.00/)).toBeNull();
    expect(within(dialog).getAllByText("Billed monthly").length).toBeGreaterThan(0);
    expect(screen.queryByText(/flat/)).toBeNull();
  });
});

describe("SubscriptionsPage — overdue", () => {
  const overdue = {
    ...base,
    id: "s1",
    product_key: "operations",
    state: "past_due",
    entitled_products: ["operations"],
    dunning: {
      cycle_id: "cy1",
      opened_at: "2026-10-01T00:00:00Z",
      grace_ends_at: "2026-10-15T00:00:00Z",
      next_attempt_at: null,
      attempts_made: 1,
      max_attempts: 4,
      amount_minor: 499900,
      currency: "INR",
    },
  };

  it("sends whoever can subscribe and read invoices to Invoices to pay", async () => {
    vi.mocked(api.listProducts).mockResolvedValue([{ key: "operations", name: "OpsGrids Inventory", is_bundle: false }] as never);
    vi.mocked(api.listSubscriptions).mockResolvedValue([overdue] as never);
    render(
      <MemoryRouter>
        <PermissionProvider grants={["core.product.activation.write:ORG", "core.billing.invoice.read:ORG"]}>
          <SubscriptionsPage />
        </PermissionProvider>
      </MemoryRouter>,
    );
    const link = await screen.findByRole("link", { name: "Pay now for Inventory" });
    expect(link.getAttribute("href")).toBe("/invoices");
  });

  it("does not link a role that may pay but cannot read invoices to a page it would be refused", async () => {
    vi.mocked(api.listProducts).mockResolvedValue([{ key: "operations", name: "OpsGrids Inventory", is_bundle: false }] as never);
    vi.mocked(api.listSubscriptions).mockResolvedValue([overdue] as never);
    render(
      <MemoryRouter>
        <PermissionProvider grants={["core.product.activation.write:ORG"]}>
          <SubscriptionsPage />
        </PermissionProvider>
      </MemoryRouter>,
    );
    expect(await screen.findByText(/Ask your organisation's owner to pay it/)).toBeTruthy();
    expect(screen.queryByRole("link", { name: /Pay now/ })).toBeNull();
  });

  it("offers no pay link without the subscribe permission", async () => {
    vi.mocked(api.listProducts).mockResolvedValue([{ key: "operations", name: "OpsGrids Inventory", is_bundle: false }] as never);
    vi.mocked(api.listSubscriptions).mockResolvedValue([overdue] as never);
    render(
      <MemoryRouter>
        <PermissionProvider grants={["core.product.activation.read:ORG"]}>
          <SubscriptionsPage />
        </PermissionProvider>
      </MemoryRouter>,
    );
    expect(await screen.findByText(/Payment overdue/)).toBeTruthy();
    expect(screen.queryByRole("link", { name: /Pay now/ })).toBeNull();
  });
});
