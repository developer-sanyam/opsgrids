import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as client from "@/api/client";
import type { OrganizationPricing, OverrideResult } from "@/api/types";
import { CompGrantAction, SuspendAction } from "./AccountActions";

/**
 * The account actions are `@opsgrids/ui` dialogs, not a local modal: a real dialog with a scrolling
 * body and a pinned submit (so the button is reachable on a phone), inline field errors instead of
 * a silently disabled submit, and the endpoint note folded away in a disclosure.
 */

const ORG = { organization_id: "org_test", name: "Zenith Polymers" };

const RESULT: OverrideResult = {
  action: "suspend",
  organization_id: "org_test",
  delegated_to: "subscription",
  result: {},
};

function pricing(): OrganizationPricing {
  return {
    organization_id: "org_test",
    products: [
      {
        product_key: "crm",
        product_name: "OpsGrids CRM",
        subscription: {
          id: "sub_1",
          state: "active",
          plan_version_id: "pv_1",
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
        current_price: null,
        public_price: null,
        history: [],
      },
    ],
  };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("SuspendAction", () => {
  it("opens as a dialog whose submit is inside it, and keeps the endpoint note collapsed", async () => {
    vi.spyOn(client, "getOrganizationPricing").mockResolvedValue(pricing());
    render(<SuspendAction org={ORG} onClose={vi.fn()} onDone={vi.fn()} />);

    const dialog = await screen.findByRole("dialog", { name: "Suspend Zenith Polymers" });
    // Suspend is destructive, so the submit is red and not the accent.
    expect(within(dialog).getByRole("button", { name: "Suspend account" }).className).toContain("bg-negative");
    expect(within(dialog).getByRole("button", { name: "Cancel" })).toBeTruthy();

    const details = dialog.querySelector("details");
    expect(details).not.toBeNull();
    expect(details?.open).toBe(false);
    expect(within(dialog).getByText("Details")).toBeTruthy();
  });

  it("explains an empty reason on the field rather than leaving the submit disabled", async () => {
    const post = vi.spyOn(client, "postOverride").mockResolvedValue(RESULT);
    vi.spyOn(client, "getOrganizationPricing").mockResolvedValue(pricing());
    render(<SuspendAction org={ORG} onClose={vi.fn()} onDone={vi.fn()} />);

    const submit = await screen.findByRole("button", { name: "Suspend account" });
    await waitFor(() => expect(screen.getByRole("radio", { name: /OpsGrids CRM/ })).toBeTruthy());
    expect((submit as HTMLButtonElement).disabled).toBe(false);
    await userEvent.click(submit);

    expect(screen.getByText("Enter a reason.")).toBeTruthy();
    expect(post).not.toHaveBeenCalled();
  });

  it("submits the reason and shows the accepted result", async () => {
    const post = vi.spyOn(client, "postOverride").mockResolvedValue(RESULT);
    vi.spyOn(client, "getOrganizationPricing").mockResolvedValue(pricing());
    const onDone = vi.fn();
    render(<SuspendAction org={ORG} onClose={vi.fn()} onDone={onDone} />);

    await waitFor(() => expect(screen.getByRole("radio", { name: /OpsGrids CRM/ })).toBeTruthy());
    await userEvent.type(screen.getByLabelText("Reason (required)"), "Non-payment");
    await userEvent.click(screen.getByRole("button", { name: "Suspend account" }));

    await waitFor(() => expect(screen.getByText("Override accepted")).toBeTruthy());
    expect(post).toHaveBeenCalledWith({
      action: "suspend",
      organization_id: "org_test",
      subscription_id: "sub_1",
      reason: "Non-payment",
    });
    expect(onDone).toHaveBeenCalledWith(RESULT);
  });
});

describe("CompGrantAction", () => {
  it("flags a missing amount and reason on their fields", async () => {
    const post = vi.spyOn(client, "postOverride").mockResolvedValue(RESULT);
    render(<CompGrantAction org={ORG} onClose={vi.fn()} onDone={vi.fn()} />);

    await userEvent.click(screen.getByRole("button", { name: "Grant credit" }));

    expect(screen.getByText("Enter an amount greater than zero.")).toBeTruthy();
    expect(screen.getByText("Enter a reason.")).toBeTruthy();
    expect(post).not.toHaveBeenCalled();
  });
});
