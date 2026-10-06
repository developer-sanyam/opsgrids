import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as catalog from "@/api/catalog";
import type { PlanDetailOut, PlanListItemOut, PlanListOut, PlanVersionOut } from "@/api/catalog";
import { ApiError } from "@/api/http";
import * as authContext from "@/auth/context";
import type { AuthContextValue } from "@/auth/context";
import type { OperatorRole } from "@/auth/types";
import { PlansPage } from "./Plans";

/**
 * Plans & prices end to end against a mocked catalog client: every list state, the role hints in
 * both directions (support sees nothing, billing_ops reads, owner writes), and the write dialogs —
 * reason required, one `Idempotency-Key` per dialog open reused on retry, the server's `errors[]`
 * shown verbatim, and the result announced in a live region.
 */

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

function version(overrides: Partial<PlanVersionOut> = {}): PlanVersionOut {
  return {
    id: "v3",
    plan_id: "plan-1",
    plan_name: "Growth",
    product_key: "hrms",
    plan_state: "active",
    version_no: 3,
    state: "draft",
    billing_interval: "month",
    currency: "INR",
    restricted_to_org_id: null,
    published_at: null,
    prices: [
      { id: "p1", kind: "flat", amount_minor: 149_900, currency: "INR", unit: null, unit_price_micros: null, meter_key: null },
    ],
    features: [{ feature_key: "trial_days", value: 14 }],
    seat_classes: [],
    ...overrides,
  };
}

function row(overrides: Partial<PlanListItemOut> = {}): PlanListItemOut {
  return {
    id: "plan-1",
    product_key: "hrms",
    code: "growth",
    name: "Growth",
    state: "active",
    sort_order: 20,
    created_at: "2026-09-01T00:00:00Z",
    counts: { draft: 1, published: 1, grandfathered: 0, bespoke: 2 },
    current: version({ id: "v2", version_no: 2, state: "published", published_at: "2026-09-02T00:00:00Z" }),
    latest_version_no: 3,
    ...overrides,
  };
}

function page(items: PlanListItemOut[], next_cursor: string | null = null): PlanListOut {
  return { items, next_cursor };
}

function detail(overrides: Partial<PlanDetailOut> = {}): PlanDetailOut {
  return {
    id: "plan-1",
    product_key: "hrms",
    code: "growth",
    name: "Growth",
    state: "active",
    sort_order: 20,
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-01T00:00:00Z",
    counts: { draft: 1, published: 1, grandfathered: 0, bespoke: 2 },
    latest_version_no: 3,
    versions: [
      {
        id: "v3",
        plan_id: "plan-1",
        version_no: 3,
        state: "draft",
        billing_interval: "month",
        currency: "INR",
        restricted_to_org_id: null,
        published_at: null,
        created_at: "2026-10-01T00:00:00Z",
        price_count: 1,
        feature_count: 1,
      },
    ],
    next_cursor: null,
    ...overrides,
  };
}

function renderPage(path = "/plans") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <PlansPage />
    </MemoryRouter>,
  );
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("who sees what", () => {
  it("shows support a notice and never calls the catalogue", () => {
    mockAuth(["support"]);
    const list = vi.spyOn(catalog, "listPlans");
    renderPage();
    expect(screen.getByText("Plans & prices need the owner or billing role")).toBeTruthy();
    expect(list).not.toHaveBeenCalled();
  });

  it("lets billing_ops read the table and a plan, with no way to change either", async () => {
    mockAuth(["billing_ops"]);
    vi.spyOn(catalog, "listPlans").mockResolvedValue(page([row()]));
    vi.spyOn(catalog, "getPlan").mockResolvedValue(detail());
    vi.spyOn(catalog, "getVersion").mockResolvedValue(version());
    renderPage();
    await screen.findByText("INR 1,499.00");
    expect(screen.queryByRole("button", { name: "New plan" })).toBeNull();

    await userEvent.click(screen.getByRole("button", { name: "Open Growth" }));
    await screen.findByText("Read-only: changing the catalogue needs the owner role.");
    await userEvent.click(screen.getByRole("button", { name: /Show sheet/ }));
    await screen.findByText("Prices");
    expect(screen.queryByRole("button", { name: /Publish/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /New draft/ })).toBeNull();
  });
});

describe("the plans table", () => {
  it("asks for the public audience by default, then the filter's", async () => {
    mockAuth(["owner"]);
    const list = vi.spyOn(catalog, "listPlans").mockResolvedValue(page([row()]));
    renderPage();
    await screen.findByText("Growth");
    expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ audience: "public" }));

    await userEvent.selectOptions(screen.getByLabelText("Audience"), "restricted");
    await waitFor(() =>
      expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ audience: "restricted" })),
    );
  });

  it("renders the server's error verbatim and retries", async () => {
    mockAuth(["owner"]);
    const list = vi
      .spyOn(catalog, "listPlans")
      .mockRejectedValueOnce(new Error("catalog is unreachable"))
      .mockResolvedValueOnce(page([row()]));
    renderPage();
    await screen.findByText("catalog is unreachable");
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    await screen.findByText("Growth");
    expect(list).toHaveBeenCalledTimes(2);
  });

  it("says what to do when nothing matches", async () => {
    mockAuth(["owner"]);
    vi.spyOn(catalog, "listPlans").mockResolvedValue(page([]));
    renderPage();
    await screen.findByText("No plans match");
  });

  it("loads the next page with the cursor and appends it", async () => {
    mockAuth(["owner"]);
    const list = vi
      .spyOn(catalog, "listPlans")
      .mockResolvedValueOnce(page([row()], "cursor-1"))
      .mockResolvedValueOnce(page([row({ id: "plan-2", name: "Starter", code: "starter" })]));
    renderPage();
    await screen.findByText("Growth");
    await userEvent.click(screen.getByRole("button", { name: "Load more plans" }));
    await screen.findByText("Starter");
    expect(screen.getByText("Growth")).toBeTruthy();
    expect(list).toHaveBeenLastCalledWith(expect.objectContaining({ cursor: "cursor-1" }));
    expect(screen.queryByRole("button", { name: "Load more plans" })).toBeNull();
  });
});

describe("owner writes", () => {
  async function openDraftSheet() {
    mockAuth(["owner"]);
    vi.spyOn(catalog, "getPlan").mockResolvedValue(detail());
    vi.spyOn(catalog, "getVersion").mockResolvedValue(version());
    renderPage("/plans?plan=plan-1");
    await userEvent.click(await screen.findByRole("button", { name: /Show sheet/ }));
    await screen.findByRole("button", { name: "Publish v3" });
  }

  it("publish says it is permanent, needs a reason, and sends one key across retries", async () => {
    await openDraftSheet();
    const publish = vi
      .spyOn(catalog, "publishVersion")
      .mockRejectedValueOnce(
        new ApiError(422, "plan version 3 cannot be published: price #1 (flat) is zero", {
          detail: "plan version 3 cannot be published: price #1 (flat) is zero",
          request_id: "req-123",
          errors: [{ field: "prices.0.amount_minor", code: "zero_amount", message: "price #1 (flat) is zero" }],
        } as never),
      )
      .mockResolvedValueOnce(version({ state: "published" }));

    await userEvent.click(screen.getByRole("button", { name: "Publish v3" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Publishing is permanent.")).toBeTruthy();

    await userEvent.click(within(dialog).getByRole("button", { name: "Publish v3 permanently" }));
    expect(publish).not.toHaveBeenCalled();
    expect(within(dialog).getByText("Say why, in at least 3 characters.")).toBeTruthy();

    await userEvent.type(within(dialog).getByLabelText(/Reason/), "Launch pricing");
    await userEvent.click(within(dialog).getByRole("button", { name: "Publish v3 permanently" }));
    await within(dialog).findByText("price #1 (flat) is zero");
    expect(within(dialog).getByText(/req-123/)).toBeTruthy();

    await userEvent.click(within(dialog).getByRole("button", { name: "Publish v3 permanently" }));
    await screen.findByText("v3 of Growth is published.");
    expect(publish).toHaveBeenCalledTimes(2);
    const [firstKey, secondKey] = publish.mock.calls.map((call) => call[2]);
    expect(firstKey).toBeTruthy();
    expect(secondKey).toBe(firstKey);
    expect(publish.mock.calls[0]?.[1]).toEqual({ reason: "Launch pricing", allow_zero_amounts: false });
  });

  it("sends a double-clicked publish once", async () => {
    await openDraftSheet();
    let release: (value: PlanVersionOut) => void = () => undefined;
    const publish = vi.spyOn(catalog, "publishVersion").mockReturnValue(
      new Promise<PlanVersionOut>((resolve) => {
        release = resolve;
      }),
    );
    await userEvent.click(screen.getByRole("button", { name: "Publish v3" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText(/Reason/), "Launch pricing");
    const submit = within(dialog).getByRole("button", { name: "Publish v3 permanently" });
    await userEvent.dblClick(submit);
    release(version({ state: "published" }));
    await screen.findByText("v3 of Growth is published.");
    expect(publish).toHaveBeenCalledTimes(1);
  });

  it("drafts a new version from a full, prefilled sheet", async () => {
    await openDraftSheet();
    const create = vi.spyOn(catalog, "createVersion").mockResolvedValue(version({ id: "v4", version_no: 4 }));
    await userEvent.click(screen.getByRole("button", { name: "New draft from v3" }));
    const dialog = await screen.findByRole("dialog");
    const amount = within(dialog).getByLabelText(/Amount \(INR\)/);
    expect((amount as HTMLInputElement).value).toBe("1499.00");
    await userEvent.clear(amount);
    await userEvent.type(amount, "1,599.50");
    await userEvent.type(within(dialog).getByLabelText(/Reason/), "Price rise");
    await userEvent.click(within(dialog).getByRole("button", { name: "Create draft" }));

    await screen.findByText("Draft v4 of Growth created.");
    expect(create).toHaveBeenCalledWith(
      "plan-1",
      {
        billing_interval: "month",
        currency: "INR",
        restricted_to_org_id: null,
        prices: [{ kind: "flat", currency: "INR", amount_minor: 159_950, unit: null, unit_price_micros: null, meter_key: null }],
        features: [{ feature_key: "trial_days", value: 14 }],
        seat_classes: [],
        reason: "Price rise",
      },
      expect.any(String),
    );
  });

  it("withdraws a published version with the consequence spelled out", async () => {
    mockAuth(["owner"]);
    vi.spyOn(catalog, "getPlan").mockResolvedValue(
      detail({
        versions: [
          {
            id: "v2",
            plan_id: "plan-1",
            version_no: 2,
            state: "published",
            billing_interval: "month",
            currency: "INR",
            restricted_to_org_id: null,
            published_at: "2026-09-02T00:00:00Z",
            created_at: "2026-09-02T00:00:00Z",
            price_count: 1,
            feature_count: 1,
          },
        ],
      }),
    );
    vi.spyOn(catalog, "getVersion").mockResolvedValue(version({ id: "v2", version_no: 2, state: "published" }));
    const withdraw = vi.spyOn(catalog, "withdrawVersion").mockResolvedValue(version({ id: "v2", version_no: 2, state: "grandfathered" }));
    renderPage("/plans?plan=plan-1");
    await userEvent.click(await screen.findByRole("button", { name: /Show sheet/ }));
    await userEvent.click(await screen.findByRole("button", { name: "Withdraw v2" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/keep it and keep being billed/)).toBeTruthy();
    await userEvent.type(within(dialog).getByLabelText(/Reason/), "Superseded by v3");
    await userEvent.click(within(dialog).getByRole("button", { name: "Withdraw v2" }));
    await screen.findByText("v2 of Growth is withdrawn.");
    expect(withdraw).toHaveBeenCalledWith("v2", "Superseded by v3", expect.any(String));
  });

  it("publishes a deliberate zero only when the switch is turned on", async () => {
    mockAuth(["owner"]);
    vi.spyOn(catalog, "getPlan").mockResolvedValue(detail());
    const free = version({
      prices: [{ id: "p1", kind: "flat", amount_minor: 0, currency: "INR", unit: null, unit_price_micros: null, meter_key: null }],
    });
    vi.spyOn(catalog, "getVersion").mockResolvedValue(free);
    const publish = vi.spyOn(catalog, "publishVersion").mockResolvedValue({ ...free, state: "published" });
    renderPage("/plans?plan=plan-1");
    await userEvent.click(await screen.findByRole("button", { name: /Show sheet/ }));
    await userEvent.click(await screen.findByRole("button", { name: "Publish v3" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText(/Reason/), "Free tier");
    await userEvent.click(within(dialog).getByRole("switch", { name: /deliberately prices something at zero/ }));
    await userEvent.click(within(dialog).getByRole("button", { name: "Publish v3 permanently" }));
    await screen.findByText("v3 of Growth is published.");
    expect(publish.mock.calls[0]?.[1]).toEqual({ reason: "Free tier", allow_zero_amounts: true });
  });

  it("says a lost connection leaves the outcome unknown and keeps the key for the retry", async () => {
    await openDraftSheet();
    const publish = vi
      .spyOn(catalog, "publishVersion")
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValueOnce(version({ state: "published" }));
    await userEvent.click(screen.getByRole("button", { name: "Publish v3" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText(/Reason/), "Launch pricing");
    await userEvent.click(within(dialog).getByRole("button", { name: "Publish v3 permanently" }));
    await within(dialog).findByText(/Failed to fetch The outcome is unknown — retry from this dialog/);
    await userEvent.click(within(dialog).getByRole("button", { name: "Publish v3 permanently" }));
    await screen.findByText("v3 of Growth is published.");
    expect(publish.mock.calls[1]?.[2]).toBe(publish.mock.calls[0]?.[2]);
  });

  it("deletes a draft with a reason and a key", async () => {
    await openDraftSheet();
    const remove = vi
      .spyOn(catalog, "deleteDraftVersion")
      .mockResolvedValue({ id: "v3", plan_id: "plan-1", version_no: 3, deleted: true });
    await userEvent.click(screen.getByRole("button", { name: "Delete draft" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText(/Reason/), "Typo sheet");
    await userEvent.click(within(dialog).getByRole("button", { name: "Delete draft" }));
    await screen.findByText("Draft v3 of Growth deleted.");
    expect(remove).toHaveBeenCalledWith("v3", "Typo sheet", expect.any(String));
  });

  it("retire says it does not stop a pin by id, then reactivate sends its own reason", async () => {
    mockAuth(["owner"]);
    vi.spyOn(catalog, "getPlan")
      .mockResolvedValueOnce(detail())
      .mockResolvedValue(detail({ state: "retired" }));
    const retire = vi.spyOn(catalog, "retirePlan").mockResolvedValue(row({ state: "retired" }));
    const reactivate = vi.spyOn(catalog, "reactivatePlan").mockResolvedValue(row());
    renderPage("/plans?plan=plan-1");
    await userEvent.click(await screen.findByRole("button", { name: "Retire plan" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).queryByText(/can no longer choose/)).toBeNull();
    expect(within(dialog).getByText("Retiring does not stop a version being chosen by id.")).toBeTruthy();
    expect(within(dialog).getByText(/Withdraw each published version too/)).toBeTruthy();
    await userEvent.type(within(dialog).getByLabelText(/Reason/), "Stop selling");
    await userEvent.click(within(dialog).getByRole("button", { name: "Retire plan" }));
    await screen.findByText("Growth is retired.");
    expect(retire).toHaveBeenCalledWith("plan-1", "Stop selling", expect.any(String));

    await userEvent.click(await screen.findByRole("button", { name: "Reactivate plan" }));
    const again = await screen.findByRole("dialog");
    await userEvent.type(within(again).getByLabelText(/Reason/), "Selling again");
    await userEvent.click(within(again).getByRole("button", { name: "Reactivate plan" }));
    await screen.findByText("Growth is active again.");
    expect(reactivate).toHaveBeenCalledWith("plan-1", "Selling again", expect.any(String));
    expect(reactivate.mock.calls[0]?.[2]).not.toBe(retire.mock.calls[0]?.[2]);
  });

  it("withdraw warns that a scheduled change still lands on the version", async () => {
    mockAuth(["owner"]);
    vi.spyOn(catalog, "getPlan").mockResolvedValue(
      detail({ versions: [{ ...detail().versions[0]!, id: "v2", version_no: 2, state: "published" }] }),
    );
    vi.spyOn(catalog, "getVersion").mockResolvedValue(version({ id: "v2", version_no: 2, state: "published" }));
    renderPage("/plans?plan=plan-1");
    await userEvent.click(await screen.findByRole("button", { name: /Show sheet/ }));
    await userEvent.click(await screen.findByRole("button", { name: "Withdraw v2" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).queryByText(/Nobody new can choose it/)).toBeNull();
    expect(within(dialog).getByText(/does not cancel a change already scheduled onto this version/)).toBeTruthy();
  });

  describe("the header's New draft", () => {
    const published = {
      ...detail().versions[0]!,
      id: "v2",
      version_no: 2,
      state: "published" as const,
      published_at: "2026-09-02T00:00:00Z",
    };
    const sheet = version({
      id: "v2",
      version_no: 2,
      state: "published",
      features: [
        { feature_key: "bundled_products", value: ["hrms", "crm"] },
        { feature_key: "trial_days", value: 30 },
      ],
    });

    it("copies the version on sale, whole, even when a newer draft exists", async () => {
      mockAuth(["owner"]);
      vi.spyOn(catalog, "getPlan").mockResolvedValue(detail({ versions: [detail().versions[0]!, published] }));
      const get = vi.spyOn(catalog, "getVersion").mockResolvedValue(sheet);
      renderPage("/plans?plan=plan-1");
      await screen.findByText("v2");
      await userEvent.click(screen.getByRole("button", { name: "New draft" }));
      const dialog = await screen.findByRole("dialog");
      expect(get).toHaveBeenCalledWith("v2");
      expect(within(dialog).getByText(/Starts as a copy of version 2/)).toBeTruthy();
      expect((within(dialog).getByLabelText(/Trial length/) as HTMLInputElement).value).toBe("30");
      expect((within(dialog).getByLabelText(/Bundled products/) as HTMLInputElement).value).toBe("hrms, crm");
    });

    it("opens nothing when the copy cannot be loaded, and says so", async () => {
      mockAuth(["owner"]);
      vi.spyOn(catalog, "getPlan").mockResolvedValue(detail({ versions: [published] }));
      vi.spyOn(catalog, "getVersion")
        .mockRejectedValueOnce(new ApiError(502, "Bad gateway", { detail: "Bad gateway", request_id: "req-502" } as never))
        .mockResolvedValueOnce(sheet);
      renderPage("/plans?plan=plan-1");
      await screen.findByText("v2");
      await userEvent.click(screen.getByRole("button", { name: "New draft" }));
      await screen.findByText("Could not load v2 to copy — try New draft again.");
      expect(screen.queryByRole("dialog")).toBeNull();
      expect(screen.getByText(/req-502/)).toBeTruthy();

      await userEvent.click(screen.getByRole("button", { name: "New draft" }));
      const dialog = await screen.findByRole("dialog");
      expect(within(dialog).getByText(/Starts as a copy of version 2/)).toBeTruthy();
      expect((within(dialog).getByLabelText(/Trial length/) as HTMLInputElement).value).toBe("30");
    });
  });

  it("creates a plan and announces it", async () => {
    mockAuth(["owner"]);
    vi.spyOn(catalog, "listPlans").mockResolvedValue(page([]));
    const create = vi.spyOn(catalog, "createPlan").mockResolvedValue(row({ id: "plan-9", name: "Scale", code: "scale" }));
    renderPage();
    await screen.findByText("No plans match");
    await userEvent.click(screen.getByRole("button", { name: "New plan" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(within(dialog).getByLabelText(/Name/), "Scale");
    await userEvent.type(within(dialog).getByLabelText(/^Code/), "scale");
    await userEvent.type(within(dialog).getByLabelText(/Reason/), "New tier");
    await userEvent.click(within(dialog).getByRole("button", { name: "Create plan" }));
    await screen.findByText("Scale created. Draft a version to give it a price.");
    expect(create).toHaveBeenCalledWith(
      expect.objectContaining({ code: "scale", name: "Scale", reason: "New tier", sort_order: 100 }),
      expect.any(String),
    );
  });
});
